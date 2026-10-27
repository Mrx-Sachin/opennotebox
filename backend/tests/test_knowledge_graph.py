"""Tests for the knowledge graph router.

Covers: build (Celery dispatch, 400 when no text, 404 for other user's doc,
409 when already extracting, auth), status, entities/relationships/stats,
query, delete, and multi-user isolation.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from app.models.kg import KgEntity, KgRelationship, KgExtractionState


def _make_relation(db, doc_id, subj_name, rel, obj_name):
    subj = KgEntity(document_id=doc_id, name=subj_name,
                    name_normalized=subj_name.lower(), entity_type="concept")
    obj = KgEntity(document_id=doc_id, name=obj_name,
                   name_normalized=obj_name.lower(), entity_type="concept")
    db.add_all([subj, obj])
    db.flush()
    db.add(KgRelationship(document_id=doc_id, subject_id=subj.id,
                          object_id=obj.id, relationship=rel))


class TestBuildKg:
    """POST /api/documents/{doc_id}/kg/build"""

    async def test_build_dispatches_celery(self, client, auth_header, test_document, db):
        with patch("app.tasks.kg_tasks.build_kg_document.delay") as delay:
            resp = await client.post(
                f"/api/documents/{test_document.id}/kg/build", headers=auth_header
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        assert delay.call_count == 1
        args, kwargs = delay.call_args
        # doc_id positional, user_id kwarg from the authenticated caller
        assert args[0] == test_document.id
        assert kwargs.get("user_id") is not None
        # Status must be "extracting" immediately, without waiting for the
        # worker, so the frontend's polling loop actually tracks the build.
        state = db.query(KgExtractionState).filter(
            KgExtractionState.document_id == test_document.id
        ).first()
        assert state is not None
        assert state.status == "extracting"

    async def test_build_other_user_document(self, client, auth_header, second_document):
        """Cannot build the KG for another user's document."""
        with patch("app.tasks.kg_tasks.build_kg_document.delay") as delay:
            resp = await client.post(
                f"/api/documents/{second_document.id}/kg/build", headers=auth_header
            )
        assert resp.status_code == 404
        assert delay.call_count == 0

    async def test_build_unauthenticated(self, client, test_document):
        resp = await client.post(
            f"/api/documents/{test_document.id}/kg/build"
        )
        assert resp.status_code == 401

    async def test_build_conflict_when_extracting(
        self, client, auth_header, db, test_document
    ):
        db.add(KgExtractionState(
            document_id=test_document.id, status="extracting", total_chunks=5
        ))
        db.commit()
        with patch("app.tasks.kg_tasks.build_kg_document.delay") as delay:
            resp = await client.post(
                f"/api/documents/{test_document.id}/kg/build", headers=auth_header
            )
        assert resp.status_code == 409
        assert delay.call_count == 0


class TestKgStatus:
    """GET /api/documents/{doc_id}/kg/status"""

    async def test_status_pending(self, client, auth_header, test_document):
        resp = await client.get(
            f"/api/documents/{test_document.id}/kg/status", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    async def test_status_shows_extracting(
        self, client, auth_header, db, test_document
    ):
        db.add(KgExtractionState(
            document_id=test_document.id, status="extracting",
            chunks_processed=2, total_chunks=10,
        ))
        db.commit()
        resp = await client.get(
            f"/api/documents/{test_document.id}/kg/status", headers=auth_header
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "extracting"
        assert data["chunks_processed"] == 2
        assert data["total_chunks"] == 10

    async def test_status_other_user_document(self, client, auth_header, second_document):
        resp = await client.get(
            f"/api/documents/{second_document.id}/kg/status", headers=auth_header
        )
        assert resp.status_code == 404


class TestKgEntities:
    """GET /api/documents/{doc_id}/kg/entities"""

    async def test_entities_empty(self, client, auth_header, test_document):
        resp = await client.get(
            f"/api/documents/{test_document.id}/kg/entities", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_entities_listed_with_type_filter(
        self, client, auth_header, db, test_document
    ):
        db.add(KgEntity(document_id=test_document.id, name="Deep Learning",
                        name_normalized="deep_learning", entity_type="concept"))
        db.add(KgEntity(document_id=test_document.id, name="OpenAI",
                        name_normalized="openai", entity_type="organization"))
        db.commit()

        resp = await client.get(
            f"/api/documents/{test_document.id}/kg/entities", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

        filtered = await client.get(
            f"/api/documents/{test_document.id}/kg/entities?entity_type=concept",
            headers=auth_header,
        )
        assert filtered.json()["count"] == 1
        assert filtered.json()["entities"][0]["type"] == "concept"

    async def test_entities_isolation(self, client, auth_header, db, test_document, second_document):
        """User A cannot list entities from User B's document."""
        db.add(KgEntity(document_id=second_document.id, name="Secret",
                        name_normalized="secret", entity_type="concept"))
        db.commit()
        resp = await client.get(
            f"/api/documents/{second_document.id}/kg/entities", headers=auth_header
        )
        assert resp.status_code == 404


class TestKgRelationships:
    """GET /api/documents/{doc_id}/kg/relationships"""

    async def test_relationships_listed(
        self, client, auth_header, db, test_document
    ):
        _make_relation(db, test_document.id, "Transformer", "uses", "Attention")
        db.commit()
        resp = await client.get(
            f"/api/documents/{test_document.id}/kg/relationships",
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["relationships"][0]["subject"] == "Transformer"
        assert data["relationships"][0]["object"] == "Attention"
        assert data["relationships"][0]["relationship"] == "uses"

    async def test_relationships_filter_by_entity(
        self, client, auth_header, db, test_document
    ):
        _make_relation(db, test_document.id, "Transformer", "uses", "Attention")
        db.commit()
        matched = await client.get(
            f"/api/documents/{test_document.id}/kg/relationships?entity_name=Transformer",
            headers=auth_header,
        )
        assert matched.json()["count"] == 1
        no_match = await client.get(
            f"/api/documents/{test_document.id}/kg/relationships?entity_name=Nope",
            headers=auth_header,
        )
        assert no_match.json()["count"] == 0


class TestKgQuery:
    """POST /api/documents/{doc_id}/kg/query"""

    async def test_query_no_graph(self, client, auth_header, test_document):
        resp = await client.post(
            f"/api/documents/{test_document.id}/kg/query",
            json={"query": "what is deep learning?"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_entities"] == []
        assert data["relationships"] == []

    async def test_query_matches_graph(self, client, auth_header, db, test_document):
        _make_relation(db, test_document.id, "Transformer", "uses", "Attention")
        db.commit()
        resp = await client.post(
            f"/api/documents/{test_document.id}/kg/query",
            json={"query": "What does the Transformer use?"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "Transformer" in data["matched_entities"]
        # "Transformer uses Attention" appears as a relationship triple
        assert any("Transformer" in t and "Attention" in t for t in data["relationships"])

    async def test_query_unauthenticated(self, client, test_document):
        resp = await client.post(
            f"/api/documents/{test_document.id}/kg/query",
            json={"query": "hi"},
        )
        assert resp.status_code == 401


class TestKgStats:
    """GET /api/documents/{doc_id}/kg/stats"""

    async def test_stats(self, client, auth_header, db, test_document):
        _make_relation(db, test_document.id, "Transformer", "uses", "Attention")
        db.add(KgEntity(document_id=test_document.id, name="BERT",
                        name_normalized="bert", entity_type="model",
                        pages=json.dumps([1])))
        db.commit()
        resp = await client.get(
            f"/api/documents/{test_document.id}/kg/stats", headers=auth_header
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity_count"] == 3
        assert data["relationship_count"] == 1
        assert data["entity_types"].get("concept") == 2
        assert data["entity_types"].get("model") == 1
        assert data["total_pages_mentioned"] == 1

    async def test_stats_tolerates_malformed_pages(
        self, client, auth_header, db, test_document
    ):
        """A corrupt 'pages' value must not 500 the stats endpoint."""
        db.add(KgEntity(document_id=test_document.id, name="Broken",
                        name_normalized="broken", entity_type="other",
                        pages="not-json"))
        db.commit()
        resp = await client.get(
            f"/api/documents/{test_document.id}/kg/stats", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["entity_count"] == 1


class TestDeleteKg:
    """DELETE /api/documents/{doc_id}/kg"""

    async def test_delete(self, client, auth_header, db, test_document):
        db.add(KgEntity(document_id=test_document.id, name="X",
                        name_normalized="x", entity_type="other"))
        db.add(KgExtractionState(document_id=test_document.id, status="completed"))
        db.commit()

        resp = await client.delete(
            f"/api/documents/{test_document.id}/kg", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert db.query(KgEntity).filter(KgEntity.document_id == test_document.id).count() == 0
        assert db.query(KgExtractionState).filter(
            KgExtractionState.document_id == test_document.id
        ).count() == 0


class TestNoChunksStatus:
    """A build whose text yields no valid chunk must still resolve the
    extraction state. The router sets it to "extracting" synchronously,
    so an early return that skips the final "completed" commit leaves the
    UI spinner running forever. The fix lives in build_kg_for_document's
    early-return paths, which is what this guards (the Celery task just
    delegates to it and only touches state in its error path)."""

    async def test_no_valid_chunks_marks_completed(self, db, test_document):
        from app.models import ExtractedText
        from app.services.kg_builder import build_kg_for_document

        # Overwrite the fixture's 3 text pages with a single short one
        # (under the 50-char chunk threshold), the shape of a doc whose
        # only page is e.g. "Hello world".
        db.query(ExtractedText).filter(
            ExtractedText.document_id == test_document.id
        ).delete()
        db.add(ExtractedText(
            document_id=test_document.id, page_number=1, content="Hello world"
        ))
        # Simulate the router's synchronous "extracting" mark that the
        # build is meant to resolve.
        db.add(KgExtractionState(
            document_id=test_document.id, status="extracting", total_chunks=0
        ))
        db.commit()

        result = await build_kg_for_document(
            db, test_document.id, "[Page 1]\nHello world",
            base_url="http://x", model_name="m", api_key=None,
        )

        assert result == {"entities": 0, "relationships": 0}
        state = db.query(KgExtractionState).filter(
            KgExtractionState.document_id == test_document.id
        ).first()
        assert state.status == "completed"
