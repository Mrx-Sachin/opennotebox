"""HybridRAG service: combine vector retrieval with knowledge graph context.

Per the HybridRAG paper (2408.04948v1): VectorRAG provides broad,
similarity-based retrieval while GraphRAG contributes structured,
relationship-rich contextual data. Together they cover each other's
blind spots.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.kg import KgExtractionState
from app.services import retrieval
from app.services.chat_engine import (
    chat_engine,
    format_source_line,
    library_document_titles,
)
from app.services.kg_retrieval import retrieve_from_graph
from app.services.metrics import get_metrics
from app.services.llm_semaphore import llm_semaphore

# Total character budget for combined sources (vector + graph).
# Graph context tends to be denser but longer; give extra room.
HYBRID_SOURCE_CHAR_BUDGET = 18_000

# Standard retrieval constants (mirrored from chat_engine).
TOP_K = 8
CANDIDATES = 24
SEMANTIC_K = 12
HISTORY_TURNS = 3


def retrieve_hybrid(
    db: Session,
    doc_ids: List[int],
    question: str,
    phrases: List[str],
    rephrasings: List[str],
    use_vector: bool = True,
    use_graph: bool = True,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Run hybrid retrieval: vector + graph, return combined result.

    Returns a dict with combined_sources, vector_sources, and graph_subgraph.
    """
    vector_sources: List[Dict[str, Any]] = []
    graph_subgraph: Dict[str, Any] = {
        "matched_entities": [],
        "triplets": [],
        "nodes": [],
        "context": "",
    }

    # Vector retrieval.
    if use_vector:
        for doc_id in doc_ids:
            retrieval.index_document(db, doc_id)

        lexical = retrieval.search(
            db, doc_ids, phrases + [question], top_k=CANDIDATES
        )
        hit_ids = [h["chunk_id"] for h in lexical if h.get("chunk_id") is not None]
        if hit_ids:
            for doc_id in doc_ids:
                retrieval.ensure_embeddings(db, doc_id, only_chunk_ids=hit_ids)
        else:
            for doc_id in doc_ids:
                retrieval.ensure_embeddings(
                    db, doc_id,
                    only_chunk_ids=retrieval.list_unembedded_chunk_ids(db, doc_id, limit=64),
                )
        retrieval.schedule_full_embeddings(doc_ids)

        semantic = retrieval.semantic_search(
            db, doc_ids, [question] + rephrasings, top_k=SEMANTIC_K
        )
        by_id = {h["chunk_id"]: h for h in lexical}
        merged = list(lexical)
        for h in semantic:
            if h["chunk_id"] in by_id:
                if h.get("sem_top"):
                    by_id[h["chunk_id"]]["sem_top"] = True
            else:
                merged.append(h)

        # Pick top candidates.
        hits = merged[:TOP_K]

        titles = library_document_titles(db, doc_ids, user_id)
        used = 0
        for h in hits:
            if used + len(h["text"]) > HYBRID_SOURCE_CHAR_BUDGET:
                break
            used += len(h["text"])
            vector_sources.append({
                "doc_id": h["doc_id"],
                "doc_title": titles.get(h["doc_id"], "Unknown"),
                "page": h["page"],
                "text": h["text"],
            })

    # Graph retrieval.
    if use_graph:
        graph_subgraph = retrieve_from_graph(db, doc_ids, question)

    # Combine sources.
    combined_sources = _build_combined_sources(
        db, doc_ids, vector_sources, graph_subgraph.get("context", "")
    )

    return {
        "combined_sources": combined_sources,
        "vector_sources": vector_sources,
        "graph_subgraph": graph_subgraph,
    }


def _build_combined_sources(
    db: Session,
    doc_ids: List[int],
    vector_sources: List[Dict[str, Any]],
    graph_context: str,
) -> List[Dict[str, Any]]:
    """Number the combined sources (vector first, graph appended)."""
    sources: List[Dict[str, Any]] = []

    for vs in vector_sources:
        sources.append({
            "n": len(sources) + 1,
            "doc_id": vs["doc_id"],
            "doc_title": vs.get("doc_title", "Document"),
            "page": vs["page"],
            "text": vs["text"],
            "source_type": "vector",
        })

    if graph_context.strip():
        sources.append({
            "n": len(sources) + 1,
            "doc_id": doc_ids[0] if doc_ids else 0,
            "doc_title": "Knowledge Graph",
            "page": None,
            "text": graph_context,
            "source_type": "graph",
        })

    return sources


def build_hybrid_prompt(
    question: str,
    sources: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Build the LLM prompt with hybrid-sourced context."""
    source_block = "\n\n".join(format_source_line(s) for s in sources)

    parts = [f"Sources:\n{source_block}"]

    if history:
        recent = history[-(HISTORY_TURNS * 2):]
        turns = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in recent)
        parts.append(f"Conversation so far:\n{turns}")

    parts.append(
        f"Question: {question}\n\n"
        "Answer with inline [n] citations after every factual claim. "
        "The sources include both text excerpts and knowledge graph facts."
    )

    return "\n\n".join(parts)


def _citation_excerpt(text: str, max_len: int = 80) -> str:
    """Short PDF-layer anchor for citation flash-highlight."""
    import re as _re

    text = (text or "").strip()
    if not text:
        return ""
    m = _re.search(r"[.!?][\"')\]]?(?:\s|$)", text[:160])
    if m:
        sentence = text[: m.end()].strip()
        if 30 <= len(sentence) <= 120:
            return sentence
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    sp = cut.rfind(" ")
    if sp >= 40:
        return cut[:sp].rstrip(" ,;:-")
    return cut.rstrip(" ,;:-")


def _used_citations(content: str, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only sources whose number actually appears as [n] in the answer."""
    import re as _re

    by_n = {s["n"]: s for s in sources}
    cited: List[int] = []
    for match in _re.finditer(r"\[(\d+(?:\s*,\s*\d+)*)\]", content):
        for raw in match.group(1).split(","):
            n = int(raw.strip())
            if n in by_n and n not in cited:
                cited.append(n)
    return [
        {
            "n": n,
            "doc_id": by_n[n]["doc_id"],
            "doc_title": by_n[n]["doc_title"],
            "page": by_n[n]["page"],
            "excerpt": _citation_excerpt(by_n[n]["text"]),
        }
        for n in cited
    ]


def _kg_built_for_docs(db: Session, doc_ids: List[int]) -> bool:
    """True if any selected document's KG extraction has finished.

    Uses the extraction state, not the entity count: a very short
    document can legitimately finish a build with zero entities, and
    that must read as "built" (so a graph-mode miss says "no matching
    entities", not "please build the graph").
    """
    if not doc_ids:
        return False
    return (
        db.query(KgExtractionState.id)
        .filter(
            KgExtractionState.document_id.in_(doc_ids),
            KgExtractionState.status == "completed",
        )
        .count()
        > 0
    )


def _no_sources_message(mode: str, kg_built: bool) -> str:
    """Explain why a hybrid/graph question came back empty.

    Graph-only mode on a document whose KG has never been built is the
    common dead end: the graph is empty, so nothing can be retrieved.
    That case says so plainly and points at the KG tab. A built graph
    with no matching entities is a genuine "not in this document", and
    hybrid mode (vector + graph) has no build prerequisite at all.
    """
    if mode == "graph" and not kg_built:
        return (
            "This document does not have a knowledge graph yet, so "
            "Graph mode has nothing to search. Open the KG tab in the "
            "assistant panel and click Build KG - once it finishes, "
            "Graph mode will answer from the extracted entities and "
            "relationships."
        )
    if mode == "graph":
        return (
            "The knowledge graph for this document has no entities "
            "matching that question. Try rephrasing, or switch to "
            "Hybrid/Standard mode to search the full text."
        )
    return (
        "I could not find anything in the selected document(s) "
        "related to that question."
    )


def _system_prompt() -> str:
    return (
        "You are a precise research assistant answering questions about the "
        "user's documents using ONLY the numbered sources provided. Every "
        "factual claim must carry an inline citation like [1] or [2, 3] "
        "referencing the source number(s) it came from. Never cite a number "
        "that is not in the source list, and never make claims the sources "
        "do not support. The sources may include both text excerpts and "
        "knowledge graph facts. If the sources truly do not bear on the "
        "question, say so plainly. Be factual and concise."
    )


async def answer_hybrid(
    question: str,
    doc_ids: List[int],
    history: Optional[List[Dict[str, str]]] = None,
    user_id: Optional[int] = None,
    mode: str = "hybrid",
) -> Dict[str, Any]:
    """Answer using hybrid (vector + graph) or graph-only retrieval.

    mode: "hybrid" runs both vector and graph retrieval.
          "graph" runs graph retrieval only.

    Note: the caller (chat_engine.answer) already increments rag_requests
    once for the request, so we do NOT count it again here.
    """
    m = get_metrics()
    t0 = time.perf_counter()

    use_vector = mode == "hybrid"
    use_graph = mode in ("hybrid", "graph")

    db = SessionLocal()
    try:
        title_map = library_document_titles(db, doc_ids, user_id)
        doc_titles = [title_map[doc_id] for doc_id in doc_ids if doc_id in title_map]

        # Rewrite query for vector retrieval (if needed).
        phrases: List[str] = []
        rephrasings: List[str] = []
        if use_vector:
            if not chat_engine.should_skip_rewrite(question, history):
                phrases, rephrasings = await chat_engine.rewrite_query(
                    question, history, doc_titles, user_id
                )

        # Run hybrid retrieval. It is fully synchronous (DB queries plus a
        # blocking Ollama embedding HTTP call) - run it in a worker thread
        # so a slow embed cannot freeze the whole event loop (and with it
        # every other in-flight request). The session is only handed to one
        # thread at a time.
        result = await asyncio.to_thread(
            retrieve_hybrid,
            db, doc_ids, question, phrases, rephrasings,
            use_vector=use_vector, use_graph=use_graph,
            user_id=user_id,
        )
        sources = result["combined_sources"]

        # Whether any selected document has a *finished* knowledge graph,
        # so the no-results message can tell "graph not built yet" apart
        # from "graph built, but nothing matches this question". A built
        # but empty graph (a very short document) still counts as built.
        kg_built = _kg_built_for_docs(db, doc_ids)
    finally:
        db.close()

    if not sources:
        return {
            "content": _no_sources_message(mode, kg_built),
            "citations": [],
            "token_count": 0,
        }

    prompt = build_hybrid_prompt(question, sources, history)

    # Get the user's active LLM model.
    model = chat_engine.model(user_id)

    async with llm_semaphore():
        from langchain_core.messages import SystemMessage, HumanMessage

        response = await model.ainvoke([
            SystemMessage(content=_system_prompt()),
            HumanMessage(content=prompt),
        ])

    content = response.content
    citations = _used_citations(content, sources)

    m.rag_latency.observe((time.perf_counter() - t0) * 1000)
    m.rag_chunk_count.observe(len(sources))

    logger.info(
        "hybrid_rag mode={} total={}ms sources={} citations={}",
        mode,
        int((time.perf_counter() - t0) * 1000),
        len(sources),
        len(citations),
    )

    return {
        "content": content,
        "citations": citations,
        "token_count": len(content.split()),
    }
