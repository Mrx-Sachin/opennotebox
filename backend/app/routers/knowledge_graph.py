"""Knowledge graph management API endpoints.

Serves the per-document knowledge graph used by HybridRAG. Routes live under
/api/documents/{doc_id}/kg/* to match the rest of the document sub-resource
endpoints (summaries, pages, ...) and the frontend client.

The build endpoint enqueues a Celery task rather than an in-process
BackgroundTask so the multi-minute LLM extraction survives a web-process
restart and shows up in Celery monitoring.
"""
import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document
from app.models.kg import KgEntity, KgRelationship, KgExtractionState
from app.routers.auth import get_current_user_from_token
from app.services.kg_builder import get_extraction_status
from app.services.kg_retrieval import retrieve_from_graph

router = APIRouter(prefix="/api/documents/{doc_id}/kg", tags=["knowledge-graph"])


def _assert_document_owned(db: Session, user_id: int, doc_id: int) -> Document:
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.user_id == user_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _entity_pages(entity: KgEntity) -> List[int]:
    """Parse the stored page list, tolerating missing or malformed values."""
    if not entity.pages:
        return []
    try:
        pages = json.loads(entity.pages)
    except (ValueError, TypeError):
        return []
    if not isinstance(pages, list):
        return []
    return [p for p in pages if isinstance(p, int)]


@router.post("/build")
def build_kg(
    doc_id: int,
    user = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Start building the knowledge graph for a document.

    The KG is built asynchronously by a Celery task. Check progress via
    GET /api/documents/{doc_id}/kg/status.
    """
    _assert_document_owned(db, user.id, doc_id)

    state = db.query(KgExtractionState).filter(
        KgExtractionState.document_id == doc_id
    ).first()
    if state and state.status == "extracting":
        raise HTTPException(
            status_code=409,
            detail="Knowledge graph extraction is already in progress",
        )

    text_rows = db.execute(
        sql_text("SELECT count(*) FROM extracted_text WHERE document_id = :d"),
        {"d": doc_id},
    ).scalar_one()
    if not text_rows:
        raise HTTPException(
            status_code=400,
            detail="No extracted text found for this document. "
                   "Document processing may not be complete.",
        )

    # Mark extraction in progress synchronously so the status endpoint is
    # truthful the moment the caller checks - the worker sets it again with
    # the real chunk count when it picks the task up.
    if state:
        state.status = "extracting"
        state.chunks_processed = 0
        state.error_message = None
    else:
        db.add(KgExtractionState(document_id=doc_id, status="extracting"))
    db.commit()

    from app.tasks.kg_tasks import build_kg_document
    build_kg_document.delay(doc_id, user_id=user.id)

    return {"status": "started", "document_id": doc_id}


@router.get("/status")
def get_kg_status(
    doc_id: int,
    user = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get the KG extraction status for a document."""
    _assert_document_owned(db, user.id, doc_id)
    status = get_extraction_status(db, doc_id)
    if not status:
        return {"document_id": doc_id, "status": "pending", "entity_count": 0, "relationship_count": 0}
    return status


@router.get("/entities")
def list_entities(
    doc_id: int,
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    limit: int = Query(100, ge=1, le=1000),
    user = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """List entities in the knowledge graph for a document."""
    _assert_document_owned(db, user.id, doc_id)

    q = db.query(KgEntity).filter(KgEntity.document_id == doc_id)
    if entity_type:
        q = q.filter(KgEntity.entity_type == entity_type)
    entities = q.order_by(KgEntity.name).limit(limit).all()

    return {
        "document_id": doc_id,
        "count": len(entities),
        "entities": [
            {
                "id": e.id,
                "document_id": doc_id,
                "name": e.name,
                "type": e.entity_type,
                "description": e.description,
                "pages_mentioned": _entity_pages(e),
                "created_at": e.created_at,
            }
            for e in entities
        ],
    }


@router.get("/relationships")
def list_relationships(
    doc_id: int,
    entity_name: Optional[str] = Query(None, description="Filter relationships involving this entity"),
    limit: int = Query(200, ge=1, le=2000),
    user = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """List relationships in the knowledge graph for a document."""
    _assert_document_owned(db, user.id, doc_id)

    q = db.query(KgRelationship).filter(KgRelationship.document_id == doc_id)

    if entity_name:
        from sqlalchemy import or_
        entity_ids = db.query(KgEntity.id).filter(
            KgEntity.document_id == doc_id,
            KgEntity.name.ilike(f"%{entity_name}%"),
        ).all()
        eid_list = [e[0] for e in entity_ids]
        if eid_list:
            q = q.filter(
                or_(
                    KgRelationship.subject_id.in_(eid_list),
                    KgRelationship.object_id.in_(eid_list),
                )
            )
        else:
            return {"document_id": doc_id, "count": 0, "relationships": []}

    rels = q.limit(limit).all()

    return {
        "document_id": doc_id,
        "count": len(rels),
        "relationships": [
            {
                "id": r.id,
                "document_id": doc_id,
                "subject": r.subject.name if r.subject else "Unknown",
                "object": r.object_entity.name if r.object_entity else "Unknown",
                "relationship": r.relationship,
                "confidence": r.confidence,
                "page": r.page_number,
            }
            for r in rels
        ],
    }


class KgQueryRequest(BaseModel):
    query: str


@router.post("/query")
def query_kg(
    doc_id: int,
    body: KgQueryRequest,
    user = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Query the knowledge graph for a question (dry-run mode)."""
    _assert_document_owned(db, user.id, doc_id)
    result = retrieve_from_graph(db, [doc_id], body.query)
    return {
        "matched_entities": result.get("matched_entities", []),
        "context": result.get("context", ""),
        "relationships": result.get("triplets", []),
    }


@router.get("/stats")
def get_kg_stats(
    doc_id: int,
    user = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get aggregate statistics about the knowledge graph for a document."""
    _assert_document_owned(db, user.id, doc_id)

    entities = db.query(KgEntity).filter(KgEntity.document_id == doc_id).all()
    rels = db.query(KgRelationship).filter(KgRelationship.document_id == doc_id).all()

    type_counts: Dict[str, int] = {}
    for e in entities:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1

    entity_ids = {e.id for e in entities}
    connectivity: Dict[int, int] = {eid: 0 for eid in entity_ids}
    for r in rels:
        if r.subject_id in entity_ids:
            connectivity[r.subject_id] = connectivity.get(r.subject_id, 0) + 1
        if r.object_id in entity_ids:
            connectivity[r.object_id] = connectivity.get(r.object_id, 0) + 1

    avg_conn = sum(connectivity.values()) / max(len(connectivity), 1)

    return {
        "document_id": doc_id,
        "entity_count": len(entities),
        "relationship_count": len(rels),
        "entity_types": type_counts,
        "avg_connectivity": round(avg_conn, 2),
        "total_pages_mentioned": len(set(
            p
            for e in entities
            for p in _entity_pages(e)
        )),
    }


@router.delete("")
def delete_kg(
    doc_id: int,
    user = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Delete the knowledge graph for a document."""
    _assert_document_owned(db, user.id, doc_id)

    db.query(KgExtractionState).filter(KgExtractionState.document_id == doc_id).delete()
    db.query(KgRelationship).filter(KgRelationship.document_id == doc_id).delete()
    db.query(KgEntity).filter(KgEntity.document_id == doc_id).delete()
    db.commit()

    return {"status": "deleted", "document_id": doc_id}
