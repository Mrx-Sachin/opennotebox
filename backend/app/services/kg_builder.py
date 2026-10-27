"""Knowledge graph builder: chunk, abstract, extract triplets, persist.

Two-tiered LLM chain per the HybridRAG paper (2408.04948v1):
  Tier 1 - abstract each chunk into a concise summary
  Tier 2 - extract typed entity-relationship triplets from the abstract
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from loguru import logger
from sqlalchemy.orm import Session

from app.models.kg import KgEntity, KgRelationship, KgExtractionState
from app.services.llm_semaphore import background_llm_semaphore

# Chunking params for KG extraction (larger than chat retrieval chunks
# to give the LLM enough context for meaningful entity relations).
KG_CHUNK_SIZE = 2024
KG_CHUNK_OVERLAP = 204

VALID_ENTITY_TYPES = frozenset([
    "person", "organization", "location", "concept", "event",
    "product", "technology", "metric", "date", "other",
])

COMMON_VERBS = frozenset([
    "creates", "uses", "depends_on", "part_of", "related_to",
    "causes", "prevents", "enables", "requires", "belongs_to",
    "interacts_with", "measures", "describes", "defines",
])

MAX_TRIPLETS_PER_CHUNK = 20
KG_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)


def _split_kg_chunks(pages: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """Split pages into overlapping chunks for KG extraction.

    Returns list of (page_number, chunk_text) tuples.
    """
    if not pages:
        return []

    # Join all page text with page markers.
    full_text = "\n\n".join(f"[Page {pn}]\n{text}" for pn, text in pages)

    chunks: List[Tuple[int, str]] = []
    start = 0
    while start < len(full_text):
        end = start + KG_CHUNK_SIZE
        chunk = full_text[start:end]

        # Extract the first page number mentioned in the chunk as its anchor.
        page_match = re.search(r"\[Page (\d+)\]", chunk)
        page_num = int(page_match.group(1)) if page_match else 1

        chunks.append((page_num, chunk))

        if end >= len(full_text):
            break

        start = end - KG_CHUNK_OVERLAP

    # Filter out chunks that are too short (less than 50 chars after
    # stripping) to avoid wasting LLM calls on noise.
    return [(pn, text) for pn, text in chunks if len(text.strip()) >= 50]


def _make_model(base_url: str, model: str, api_key: Optional[str]) -> ChatOpenAI:
    """Build a ChatOpenAI client for KG extraction."""
    return ChatOpenAI(
        base_url=base_url,
        model=model,
        api_key=api_key or "ollama",
        timeout=KG_TIMEOUT,
        max_retries=1,
        temperature=0.0,
        max_tokens=1536,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


async def _extract_abstract(
    model: ChatOpenAI, text: str
) -> Optional[str]:
    """Tier 1: Abstract a document chunk into a concise summary."""
    prompt = (
        "Summarize this text passage in 3-5 factual sentences. "
        "Preserve entity names, technical terms, and quantitative data. "
        "Omit filler and vague statements.\n\n"
        f"{text}"
    )
    try:
        async with background_llm_semaphore():
            response = await model.ainvoke([HumanMessage(content=prompt)])
        return response.content.strip()
    except Exception as e:
        logger.warning(f"Abstract extraction failed: {e}")
        return None


async def _extract_triplets(
    model: ChatOpenAI, abstract: str
) -> List[Dict[str, str]]:
    """Tier 2: Extract typed entity-relationship triplets from an abstract."""
    prompt = (
        "Extract entity-relationship triplets from this text. "
        "Respond with ONLY a JSON array of objects with keys: "
        '"subject", "subject_type", "relationship", "object", "object_type".\n\n'
        f"Entity types: {', '.join(sorted(VALID_ENTITY_TYPES))}\n\n"
        f"Relationships should be concise verbs like: "
        f"{', '.join(sorted(COMMON_VERBS))}\n\n"
        f"Text:\n{abstract}\n\n"
        "Output ONLY the JSON array, no explanation."
    )
    try:
        async with background_llm_semaphore():
            response = await model.ainvoke([HumanMessage(content=prompt)])
        content = response.content.strip()

        # Extract JSON array from the response.
        json_match = re.search(r"\[.*\]", content, re.DOTALL)
        if not json_match:
            logger.warning("No JSON array found in triplet extraction response")
            return []

        triplets = json.loads(json_match.group())
        if not isinstance(triplets, list):
            return []

        # Validate and clean.
        valid = []
        for t in triplets:
            if not isinstance(t, dict):
                continue
            subj = str(t.get("subject", "")).strip()
            obj = str(t.get("object", "")).strip()
            rel = str(t.get("relationship", "")).strip().lower()
            subj_type = str(t.get("subject_type", "other")).strip().lower()
            obj_type = str(t.get("object_type", "other")).strip().lower()

            if not subj or not obj or not rel:
                continue
            if subj_type not in VALID_ENTITY_TYPES:
                subj_type = "other"
            if obj_type not in VALID_ENTITY_TYPES:
                obj_type = "other"

            valid.append({
                "subject": subj,
                "subject_type": subj_type,
                "relationship": rel,
                "object": obj,
                "object_type": obj_type,
            })

        return valid[:MAX_TRIPLETS_PER_CHUNK]
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed for triplets: {e}")
        return []
    except Exception as e:
        logger.warning(f"Triplet extraction failed: {e}")
        return []


def _normalize_entity(name: str) -> str:
    """Normalize an entity name for deduplication."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower())


def _find_or_create_entity(
    db: Session,
    doc_id: int,
    name: str,
    entity_type: str,
    description: Optional[str] = None,
    page_num: Optional[int] = None,
) -> KgEntity:
    """Find an existing entity or create a new one."""
    normalized = _normalize_entity(name)

    existing = db.query(KgEntity).filter(
        KgEntity.document_id == doc_id,
        KgEntity.name_normalized == normalized,
    ).first()

    if existing:
        # Update pages if provided.
        if page_num is not None:
            pages_list = json.loads(existing.pages) if existing.pages else []
            if page_num not in pages_list:
                pages_list.append(page_num)
                existing.pages = json.dumps(pages_list)
        return existing

    entity = KgEntity(
        document_id=doc_id,
        name=name,
        name_normalized=normalized,
        entity_type=entity_type,
        description=description,
        pages=json.dumps([page_num]) if page_num is not None else None,
    )
    db.add(entity)
    db.flush()
    return entity


def _add_relationship(
    db: Session,
    doc_id: int,
    subject: KgEntity,
    obj: KgEntity,
    relationship: str,
    page_num: Optional[int] = None,
    confidence: float = 1.0,
) -> bool:
    """Add a relationship if it doesn't already exist.

    Returns True if a new row was added, False if the (subject, relationship,
    object) triple already existed for this document.
    """
    existing = db.query(KgRelationship).filter(
        KgRelationship.document_id == doc_id,
        KgRelationship.subject_id == subject.id,
        KgRelationship.relationship == relationship,
        KgRelationship.object_id == obj.id,
    ).first()

    if not existing:
        rel = KgRelationship(
            document_id=doc_id,
            subject_id=subject.id,
            object_id=obj.id,
            relationship=relationship,
            confidence=confidence,
            page_number=page_num,
        )
        db.add(rel)
        return True
    return False


async def build_kg_for_document(
    db: Session,
    doc_id: int,
    all_text: str,
    base_url: str,
    model_name: str,
    api_key: Optional[str],
) -> Dict[str, int]:
    """Build the knowledge graph for a document.

    Parses pages from all_text, chunks them, runs the two-tier LLM chain,
    and persists entities and relationships to the database.
    """
    # Parse pages from the text (format: "[Page N]\ntext\n\n[Page N+1]\ntext").
    page_pattern = re.compile(r"\[Page (\d+)\]\s*\n(.*?)(?=\n\[Page|\Z)", re.DOTALL)
    pages: List[Tuple[int, str]] = []
    for m in page_pattern.finditer(all_text):
        pages.append((int(m.group(1)), m.group(2).strip()))

    # The router marks the state "extracting" the moment a build is
    # enqueued, so every exit path from here must resolve it - an early
    # return that skips the final "completed" commit leaves the UI
    # spinner running forever (e.g. a document whose text is too short
    # to yield a single valid chunk).
    def _mark_completed(chunks_total: int = 0) -> None:
        state = db.query(KgExtractionState).filter(
            KgExtractionState.document_id == doc_id
        ).first()
        if state:
            state.status = "completed"
            state.chunks_processed = chunks_total
            db.commit()

    if not pages:
        logger.warning(f"No pages parsed for doc {doc_id}")
        _mark_completed()
        return {"entities": 0, "relationships": 0}

    chunks = _split_kg_chunks(pages)
    if not chunks:
        logger.warning(f"No valid chunks for doc {doc_id}")
        _mark_completed()
        return {"entities": 0, "relationships": 0}

    # Initialize or reset extraction state.
    state = db.query(KgExtractionState).filter(
        KgExtractionState.document_id == doc_id
    ).first()
    if not state:
        state = KgExtractionState(document_id=doc_id, total_chunks=len(chunks))
        db.add(state)
    else:
        state.status = "extracting"
        state.chunks_processed = 0
        state.error_message = None
        state.total_chunks = len(chunks)
    db.commit()

    model = _make_model(base_url, model_name, api_key)

    # Track created relationships as we go. Entity count is computed once at
    # the end - counting the full table inside the loop was O(n^2) in the
    # number of chunks.
    rel_count = 0

    for i, (page_num, chunk_text) in enumerate(chunks):
        # Tier 1: Abstract.
        abstract = await _extract_abstract(model, chunk_text)
        if not abstract:
            state.chunks_processed = i + 1
            db.commit()
            continue

        # Tier 2: Extract triplets.
        triplets = await _extract_triplets(model, abstract)

        # Persist entities and relationships.
        for t in triplets:
            subject = _find_or_create_entity(
                db, doc_id, t["subject"], t["subject_type"], page_num=page_num
            )
            obj = _find_or_create_entity(
                db, doc_id, t["object"], t["object_type"], page_num=page_num
            )
            if _add_relationship(db, doc_id, subject, obj, t["relationship"], page_num):
                rel_count += 1

        state.chunks_processed = i + 1
        db.commit()

        logger.info(
            f"KG build: doc={doc_id} chunk={i+1}/{len(chunks)} "
            f"new_rels={rel_count}"
        )

    # Mark completed.
    state.status = "completed"
    state.chunks_processed = len(chunks)
    db.commit()

    final_entities = db.query(KgEntity).filter(KgEntity.document_id == doc_id).count()
    final_rels = db.query(KgRelationship).filter(KgRelationship.document_id == doc_id).count()

    logger.info(
        f"KG build completed: doc={doc_id} entities={final_entities} relationships={final_rels}"
    )

    return {"entities": final_entities, "relationships": final_rels}


def get_extraction_status(
    db: Session, doc_id: int
) -> Optional[Dict[str, Any]]:
    """Get the KG extraction status for a document."""
    state = db.query(KgExtractionState).filter(
        KgExtractionState.document_id == doc_id
    ).first()

    if not state:
        return {
            "document_id": doc_id,
            "status": "pending",
            "chunks_processed": 0,
            "total_chunks": 0,
            "entity_count": 0,
            "relationship_count": 0,
            "error": None,
        }

    entity_count = db.query(KgEntity).filter(KgEntity.document_id == doc_id).count()
    rel_count = db.query(KgRelationship).filter(KgRelationship.document_id == doc_id).count()

    return {
        "document_id": doc_id,
        "status": state.status,
        "chunks_processed": state.chunks_processed,
        "total_chunks": state.total_chunks,
        "entity_count": entity_count,
        "relationship_count": rel_count,
        "error": state.error_message,
    }
