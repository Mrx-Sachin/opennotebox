"""Knowledge graph retrieval: match query entities and traverse the KG.

Builds a NetworkX DiGraph from the database, matches query terms to entity
names, does a depth-1 DFS to collect a subgraph, and resolves visited entities
and relationships to authentic document chunk evidence.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Set, Tuple

import networkx as nx
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.models.kg import KgEntity, KgRelationship

MAX_GRAPH_INSPECT_NODES = 10
MAX_PAGES_PER_ENTITY = 5


def build_networkx_graph(db: Session, doc_ids: List[int]) -> nx.DiGraph:
    """Load KG entities and relationships from DB into a NetworkX DiGraph."""
    G = nx.DiGraph()

    entities = db.query(KgEntity).filter(KgEntity.document_id.in_(doc_ids)).all()
    for e in entities:
        G.add_node(
            e.id,
            name=e.name,
            entity_type=e.entity_type,
            description=e.description,
            document_id=e.document_id,
            pages=e.pages,
        )

    rels = db.query(KgRelationship).filter(KgRelationship.document_id.in_(doc_ids)).all()
    for r in rels:
        if r.subject_id in G and r.object_id in G:
            G.add_edge(
                r.subject_id,
                r.object_id,
                relationship=r.relationship,
                confidence=r.confidence,
                page=r.page_number,
                document_id=r.document_id,
            )

    return G


def match_query_entities(question: str, G: nx.DiGraph) -> List[int]:
    """Match words/phrases from the question to entity names in the graph.

    Uses substring matching (case-insensitive) and word-overlap scoring
    to find the most relevant seed nodes for graph traversal.
    """
    question_lower = question.lower()
    question_words = set(re.findall(r"[a-z]{3,}", question_lower))

    if not question_words:
        return []

    candidates: Dict[int, float] = {}
    for node, data in G.nodes(data=True):
        name = (data.get("name") or "").lower()
        name_words = set(re.findall(r"[a-z]{3,}", name))

        # Direct substring match gets highest score.
        if name in question_lower:
            candidates[node] = 10.0
            continue

        # Word overlap score.
        overlap = len(question_words & name_words)
        if overlap > 0:
            score = overlap * 2.0 / max(len(name_words), 1)
            if score >= 0.5:
                candidates[node] = score

    # Return top matches sorted by score.
    sorted_matches = sorted(candidates.items(), key=lambda x: -x[1])
    return [node for node, _ in sorted_matches[:5]]


def dfs_one_hop(G: nx.DiGraph, seed_nodes: List[int]) -> Dict[str, Any]:
    """Depth-first search with depth limit 1 from seed nodes.

    Returns a dict with 'nodes' (list of node dicts) and 'triplets'
    (list of formatted relationship strings).
    """
    visited_nodes: Set[int] = set()
    triplets: List[str] = []

    for seed in seed_nodes:
        if seed not in G:
            continue
        visited_nodes.add(seed)

        # Outgoing edges.
        for neighbor in sorted(G.successors(seed)):
            if neighbor not in visited_nodes:
                visited_nodes.add(neighbor)
            edge_data = G.edges[seed, neighbor]
            subj_name = G.nodes[seed].get("name", "Unknown")
            obj_name = G.nodes[neighbor].get("name", "Unknown")
            rel = edge_data.get("relationship", "related_to")
            triplets.append(f"{subj_name} {rel} {obj_name}")

        # Incoming edges (reverse direction).
        for predecessor in sorted(G.predecessors(seed)):
            if predecessor not in visited_nodes:
                visited_nodes.add(predecessor)
            edge_data = G.edges[predecessor, seed]
            subj_name = G.nodes[predecessor].get("name", "Unknown")
            obj_name = G.nodes[seed].get("name", "Unknown")
            rel = edge_data.get("relationship", "related_to")
            triplets.append(f"{subj_name} {rel} {obj_name}")

    nodes = []
    for node_id in sorted(visited_nodes):
        data = G.nodes[node_id]
        nodes.append(
            {
                "id": node_id,
                "name": data.get("name", "Unknown"),
                "type": data.get("entity_type", "other"),
                "document_id": data.get("document_id"),
            }
        )

    return {"nodes": nodes, "triplets": triplets}


def format_graph_context(subgraph: Dict[str, Any]) -> str:
    """Format a subgraph dict into readable text for an LLM prompt."""
    parts: List[str] = []

    triplets = subgraph.get("triplets", [])
    if triplets:
        parts.append("Knowledge Graph Facts:")
        for i, t in enumerate(triplets, 1):
            parts.append(f"  {i}. {t}")

    nodes = subgraph.get("nodes", [])
    if nodes:
        parts.append("Entities Found:")
        for n in nodes:
            parts.append(f"  - {n['name']} ({n['type']})")

    return "\n".join(parts) if parts else ""


def _hub_seed_nodes(G: nx.DiGraph, limit: int = 5) -> List[int]:
    """The most-connected nodes, as a fallback when nothing matches."""
    degrees = dict(G.degree())
    return [n for n, _ in sorted(degrees.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _entity_matches_text(entity_name: str, text: str) -> bool:
    """Check if entity name or significant words appear in text."""
    if not entity_name or not text:
        return False
    name_clean = entity_name.strip().lower()
    text_clean = text.lower()
    if name_clean in text_clean:
        return True
    words = [w for w in re.findall(r"[a-z0-9]{3,}", name_clean)]
    if len(words) >= 2:
        matched = sum(1 for w in words if w in text_clean)
        if matched >= len(words) * 0.7:
            return True
    return False


def resolve_graph_candidate_chunks(
    db: Session,
    doc_ids: List[int],
    G: nx.DiGraph,
    seed_nodes: List[int],
    visited_nodes: Set[int],
    max_chunks: int = 8,
) -> List[Dict[str, Any]]:
    """Resolve visited graph entities and relationships to actual chunks in DB.

    Never fabricates pages or doc_ids. Missing or wrong page metadata is treated
    as a hint; searches the authorized document's chunk index for real matching
    text to discover the true page and chunk.

    Bounds work during resolution with deterministic order, capped queries, and early stopping.
    """
    from app.services import retrieval

    candidates: List[Dict[str, Any]] = []
    seen_chunk_keys: Set[Tuple[int, int, int]] = set()

    # Deterministic priority order: seed nodes first, then remaining visited nodes sorted by id
    remaining = sorted(visited_nodes - set(seed_nodes))
    ordered_node_ids = (seed_nodes + remaining)[:MAX_GRAPH_INSPECT_NODES]

    for node_id in ordered_node_ids:
        needed = max_chunks - len(candidates)
        if needed <= 0:
            break

        if node_id not in G:
            continue
        data = G.nodes[node_id]
        doc_id = data.get("document_id")
        if doc_id not in doc_ids:
            continue  # Authorization guard

        entity_name = data.get("name", "")
        pages_raw = data.get("pages")
        cand_pages: List[int] = []

        if pages_raw:
            try:
                parsed = json.loads(pages_raw) if isinstance(pages_raw, str) else pages_raw
                if isinstance(parsed, list):
                    cand_pages.extend(p for p in parsed if isinstance(p, int))
                elif isinstance(parsed, int):
                    cand_pages.append(parsed)
            except (TypeError, ValueError):
                pass

        # Also collect incident edge page hints
        for nbr in sorted(G.successors(node_id)):
            p = G.edges[node_id, nbr].get("page")
            if p and isinstance(p, int) and p not in cand_pages:
                cand_pages.append(p)
        for pred in sorted(G.predecessors(node_id)):
            p = G.edges[pred, node_id].get("page")
            if p and isinstance(p, int) and p not in cand_pages:
                cand_pages.append(p)

        cand_pages = sorted(set(cand_pages))[:MAX_PAGES_PER_ENTITY]

        found_on_hint = False
        if cand_pages:
            fetch_limit = min(needed * 2, 16)
            page_placeholders = ",".join(f":p{i}" for i in range(len(cand_pages)))
            page_params: Dict[str, Any] = {f"p{i}": p for i, p in enumerate(cand_pages)}
            page_params["d"] = doc_id
            page_params["limit"] = fetch_limit

            rows = db.execute(
                sql(
                    f"SELECT id, document_id, page_number, chunk_index, text "
                    f"FROM chunk WHERE document_id = :d AND page_number IN ({page_placeholders}) "
                    f"ORDER BY page_number, chunk_index LIMIT :limit"
                ),
                page_params,
            ).all()

            for cid, did, page, idx, ctext in rows:
                if _entity_matches_text(entity_name, ctext):
                    ckey = (did, page, idx)
                    if ckey not in seen_chunk_keys:
                        seen_chunk_keys.add(ckey)
                        candidates.append(
                            {
                                "chunk_id": cid,
                                "doc_id": did,
                                "page": page,
                                "chunk_index": idx,
                                "text": ctext,
                                "source_type": "document",
                                "score": 10.0 if node_id in seed_nodes else 5.0,
                            }
                        )
                        found_on_hint = True
                        if len(candidates) >= max_chunks:
                            break

        # If missing page metadata or not found on hinted pages, search document chunks
        if not found_on_hint and entity_name and len(candidates) < max_chunks:
            search_needed = max_chunks - len(candidates)
            search_hits = retrieval.search(db, [doc_id], [entity_name], top_k=min(search_needed, 2))
            for hit in search_hits:
                if hit["doc_id"] == doc_id and _entity_matches_text(entity_name, hit["text"]):
                    ckey = (hit["doc_id"], hit["page"], hit.get("chunk_index", 0))
                    if ckey not in seen_chunk_keys:
                        seen_chunk_keys.add(ckey)
                        candidates.append(
                            {
                                "chunk_id": hit["chunk_id"],
                                "doc_id": hit["doc_id"],
                                "page": hit["page"],
                                "chunk_index": hit.get("chunk_index", 0),
                                "text": hit["text"],
                                "source_type": "document",
                                "score": 8.0 if node_id in seed_nodes else 3.0,
                            }
                        )
                        if len(candidates) >= max_chunks:
                            break

    candidates.sort(key=lambda c: (-c.get("score", 0), c["page"], c.get("chunk_index", 0)))
    return candidates[:max_chunks]


def retrieve_from_graph(
    db: Session,
    doc_ids: List[int],
    question: str,
    max_chunks: int = 8,
    ensure_indexed: bool = True,
) -> Dict[str, Any]:
    """Full graph retrieval pipeline: build graph, match entities, traverse.

    Returns a dict with matched_entities, triplets, nodes, context string,
    fallback flag, and candidate_chunks resolved to authentic document text.
    """
    from app.services import retrieval

    if ensure_indexed:
        for doc_id in doc_ids:
            retrieval.index_document(db, doc_id)

    G = build_networkx_graph(db, doc_ids)

    if G.number_of_nodes() == 0:
        return {
            "matched_entities": [],
            "triplets": [],
            "nodes": [],
            "context": "",
            "fallback": False,
            "candidate_chunks": [],
        }

    seed_nodes = match_query_entities(question, G)
    fallback = False
    if not seed_nodes:
        seed_nodes = _hub_seed_nodes(G)
        fallback = bool(seed_nodes)

    subgraph = dfs_one_hop(G, seed_nodes)
    context = format_graph_context(subgraph)

    matched_names = [G.nodes[n].get("name", "") for n in seed_nodes if n in G]

    visited_nodes = {n["id"] for n in subgraph.get("nodes", [])}
    candidate_chunks = resolve_graph_candidate_chunks(
        db, doc_ids, G, seed_nodes, visited_nodes, max_chunks=max_chunks
    )

    return {
        "matched_entities": matched_names,
        "triplets": subgraph["triplets"],
        "nodes": subgraph["nodes"],
        "context": context,
        "fallback": fallback,
        "candidate_chunks": candidate_chunks,
    }
