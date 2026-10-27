"""Tests for the standard RAG path of ChatEngine.answer.

The router tests in test_chat.py mock ChatEngine.answer entirely, so they
cannot catch bugs inside the standard pipeline. This test runs the real
standard path end to end (rewrite decision, retrieval, rerank, source
assembly, prompt, LLM call) with only retrieval and the LLM itself mocked.

Regression: commit ee0aff6 renamed _should_skip_rewrite to should_skip_rewrite
but left the call site in answer() using the old underscore name, which made
every standard-mode chat request fail with
    RuntimeError: Chat failed: 'ChatEngine' object has no attribute
    '_should_skip_rewrite'
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat_engine import chat_engine


# A plain multi-word question with no reference words takes the
# should_skip_rewrite branch, so this test exercises exactly the line the
# regression lived on.
QUESTION = "What does the fox jump over?"


def _mock_llm():
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        return_value=SimpleNamespace(
            content="The fox jumps over the lazy dog. [1]"
        )
    )
    return llm


def _mock_hit(doc_id: int) -> list:
    return [
        {
            "chunk_id": 1,
            "doc_id": doc_id,
            "page": 1,
            "text": (
                "The quick brown fox jumps over the lazy dog. "
                "This is page 1 of the test document."
            ),
            "score": 10.0,
        }
    ]


class TestAnswerStandardPath:
    """ChatEngine.answer with rag_mode=None (standard BM25 + embeddings)."""

    @pytest.mark.asyncio
    async def test_answer_runs_standard_pipeline(self, db, test_document, test_user):
        with patch(
            "app.services.chat_engine.SessionLocal", return_value=db
        ), patch(
            "app.services.chat_engine.ChatEngine._retrieve",
            return_value=_mock_hit(test_document.id),
        ), patch(
            "app.services.chat_engine.ChatEngine.model",
            return_value=_mock_llm(),
        ):
            result = await chat_engine.answer(
                question=QUESTION,
                doc_ids=[test_document.id],
                user_id=test_user.id,
            )

        assert "lazy dog" in result["content"]
        assert isinstance(result["citations"], list)
        assert len(result["citations"]) == 1
        assert result["citations"][0]["page"] == 1
        assert result["citations"][0]["doc_title"] == "Test Document.pdf"

    @pytest.mark.asyncio
    async def test_follow_up_question_uses_rewrite_path(
        self, db, test_document
    ):
        # A question with history cannot skip rewriting; exercise that branch
        # of answer() too so a rename on either side is caught.
        with patch(
            "app.services.chat_engine.SessionLocal", return_value=db
        ), patch(
            "app.services.chat_engine.ChatEngine._retrieve",
            return_value=_mock_hit(test_document.id),
        ), patch(
            "app.services.chat_engine.ChatEngine.model",
            return_value=_mock_llm(),
        ), patch(
            "app.services.chat_engine.ChatEngine.rewrite_query",
            new_callable=AsyncMock,
            return_value=(["fox"], []),
        ):
            result = await chat_engine.answer(
                question="What was that again?",
                doc_ids=[test_document.id],
                history=[{"role": "user", "content": QUESTION}],
            )

        assert "lazy dog" in result["content"]
        assert len(result["citations"]) == 1

    @pytest.mark.asyncio
    async def test_streaming_standard_answer_preserves_citations(
        self, db, test_document
    ):
        class StreamingModel:
            async def astream(self, _messages):
                yield SimpleNamespace(content="The fox jumps over ")
                yield SimpleNamespace(content="the lazy dog. [1]")

        streamed = []

        async def on_token(token):
            streamed.append(token)

        with patch(
            "app.services.chat_engine.SessionLocal", return_value=db
        ), patch(
            "app.services.chat_engine.ChatEngine._retrieve",
            return_value=_mock_hit(test_document.id),
        ), patch(
            "app.services.chat_engine.ChatEngine.model",
            return_value=StreamingModel(),
        ):
            result = await chat_engine.answer(
                question=QUESTION,
                doc_ids=[test_document.id],
                on_token=on_token,
            )

        assert streamed == ["The fox jumps over ", "the lazy dog. [1]"]
        assert result["content"] == "".join(streamed)
        assert len(result["citations"]) == 1
        assert result["citations"][0]["page"] == 1
