"""Tests for the hybrid/graph RAG dead-end messages.

Graph-mode questions on a document whose KG has never been built used to
dead-end with a message that blamed the document ("could not find anything
... related to that question") instead of the real cause (no knowledge
graph yet). These tests pin the mode-aware wording and the no-sources
branch of answer_hybrid.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models.kg import KgExtractionState
from app.services import hybrid_rag


def _empty_retrieval():
    return {
        "combined_sources": [],
        "vector_sources": [],
        "graph_subgraph": {
            "matched_entities": [],
            "triplets": [],
            "nodes": [],
            "context": "",
        },
    }


class TestNoSourcesMessage:
    def test_graph_mode_without_kg_names_the_missing_graph(self):
        msg = hybrid_rag._no_sources_message("graph", kg_built=False)
        assert "does not have a knowledge graph yet" in msg
        assert "Build KG" in msg
        # The old message wrongly blamed the document's content.
        assert "could not find anything" not in msg

    def test_graph_mode_with_built_kg_is_a_match_failure(self):
        msg = hybrid_rag._no_sources_message("graph", kg_built=True)
        assert "no entities matching" in msg
        assert "does not have a knowledge graph yet" not in msg

    def test_hybrid_mode_has_no_build_prerequisite(self):
        msg = hybrid_rag._no_sources_message("hybrid", kg_built=False)
        assert "could not find anything" in msg
        assert "knowledge graph" not in msg


class TestAnswerHybridNoSources:
    """answer_hybrid with empty retrieval results returns the mode-aware
    message without calling the LLM. Retrieval is mocked so only the KG
    entity-count query (the thing that selects the wording) runs for real,
    against the test session."""

    @pytest.mark.asyncio
    async def test_graph_mode_empty_graph(self, db, test_user, test_document):
        with patch(
            "app.services.hybrid_rag.SessionLocal", return_value=db
        ), patch(
            "app.services.hybrid_rag.retrieve_hybrid", return_value=_empty_retrieval()
        ):
            result = await hybrid_rag.answer_hybrid(
                question="What is the fox about?",
                doc_ids=[test_document.id],
                user_id=test_user.id,
                mode="graph",
            )
        # No sources, so this returns before any LLM call; the assertions
        # on the message and the empty citations pin that branch.
        assert "does not have a knowledge graph yet" in result["content"]
        assert result["citations"] == []
        assert result["token_count"] == 0

    @pytest.mark.asyncio
    async def test_graph_mode_built_kg_no_matches(self, db, test_user, test_document):
        # A finished build with zero entities (e.g. a very short document)
        # still counts as built, so a graph-mode miss must say "no matching
        # entities", not "please build the graph". The signal is the
        # extraction state, not the (empty) entity table.
        db.add(
            KgExtractionState(
                document_id=test_document.id,
                status="completed",
                chunks_processed=1,
                total_chunks=1,
            )
        )
        db.commit()
        with patch(
            "app.services.hybrid_rag.SessionLocal", return_value=db
        ), patch(
            "app.services.hybrid_rag.retrieve_hybrid", return_value=_empty_retrieval()
        ):
            result = await hybrid_rag.answer_hybrid(
                question="Tell me about the fox",
                doc_ids=[test_document.id],
                user_id=test_user.id,
                mode="graph",
            )
        assert "no entities matching" in result["content"]
        assert "does not have a knowledge graph yet" not in result["content"]

    @pytest.mark.asyncio
    async def test_hybrid_mode_empty_result_stays_generic(self, db, test_user, test_document):
        with patch(
            "app.services.hybrid_rag.SessionLocal", return_value=db
        ), patch(
            "app.services.hybrid_rag.retrieve_hybrid", return_value=_empty_retrieval()
        ):
            result = await hybrid_rag.answer_hybrid(
                question="What is the fox about?",
                doc_ids=[test_document.id],
                user_id=test_user.id,
                mode="hybrid",
            )
        # Hybrid also runs vector retrieval, so a no-result is a genuine
        # "not in the document", not a build prerequisite.
        assert "could not find anything" in result["content"]
        assert "knowledge graph" not in result["content"]
