"""End-to-end HTTP/SSE tests for citation provenance and exact evidence matching.

Verifies passage segmentation, exact substring provenance, and citation persistence.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.schemas import CitationEvidence, MessageResponse
from app.services.citations import (
    build_passage_sources,
    find_passage_spans,
)
from app.services.pdf_parser import PDFParserService

PAGE_11_CHUNK = (
    "Figure 8. Zero-shot Whisper performance scales reliably across tasks and languages with increasing\n"
    "model size.\n"
    "Table 6. Empirical results across model sizes and data budgets.\n"
    "All increases in the dataset size result in improved performance on all tasks, although we see\n"
    "significant variability in improvement rates across tasks and sizes."
)

SUPPORTING_PARAGRAPH = (
    "All increases in the dataset size result in improved performance on all tasks, although we see\n"
    "significant variability in improvement rates across tasks and sizes."
)


class NonClosingSession:
    """Wraps test db session so internal try/finally close calls do not abort the test transaction."""

    def __init__(self, session):
        self._session = session

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._session, name)


@pytest.mark.asyncio
async def test_chat_stream_citation_targets_supporting_paragraph_not_figure_caption(
    client, auth_header, test_conversation, test_document, db
):
    """End-to-end SSE reproduction: citation excerpt must match the supporting claim passage."""
    conversation_id = test_conversation.id
    non_closing = NonClosingSession(db)

    mock_hit = [
        {
            "chunk_id": 101,
            "doc_id": test_document.id,
            "page": 11,
            "text": PAGE_11_CHUNK,
            "score": 12.5,
        }
    ]

    class StreamingModel:
        async def astream(self, messages):
            prompt_content = messages[1].content
            match = re.search(
                r"\[(\d+)\]\s*\([^\)]*\)\s*\n(?:(?!\[\d+\]).)*?"
                + re.escape("All increases in the dataset size"),
                prompt_content,
                re.DOTALL,
            )
            assert match is not None, f"Supporting passage not found in prompt:\n{prompt_content}"
            source_num = match.group(1)
            yield SimpleNamespace(
                content=f"According to the paper, dataset size scaling improves all tasks [{source_num}]."
            )

    with (
        patch("app.routers.chat.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.ChatEngine._retrieve", return_value=mock_hit),
        patch(
            "app.services.chat_engine.ChatEngine._rerank",
            new_callable=AsyncMock,
            return_value=mock_hit,
        ),
        patch(
            "app.services.chat_engine.ChatEngine.rewrite_query",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch("app.services.chat_engine.ChatEngine.model", return_value=StreamingModel()),
    ):
        response = await client.post(
            f"/api/chat/sessions/{conversation_id}/messages/stream",
            headers=auth_header,
            json={
                "content": "How does dataset size affect performance?",
                "document_ids": [test_document.id],
            },
        )

    assert response.status_code == 200
    events = [
        json.loads(block.removeprefix("data: "))
        for block in response.text.split("\n\n")
        if block.startswith("data: ")
    ]

    final_event = next(e for e in events if e.get("type") == "final")
    citations = final_event["message"]["citations"]

    assert len(citations) >= 1
    citation = citations[0]

    assert citation["page"] == 11
    assert "Figure 8" not in citation["excerpt"]
    assert "model size" not in citation["excerpt"]
    assert "evidence" in citation
    assert citation["evidence"] is not None
    assert "exact" in citation["evidence"]
    assert citation["evidence"]["exact"] in PAGE_11_CHUNK
    assert "All increases in the dataset size" in citation["evidence"]["exact"]
    assert "Table 6" not in citation["evidence"]["exact"]
    assert citation["excerpt"] == citation["evidence"]["exact"]


@pytest.mark.asyncio
async def test_citation_with_real_pdf_fixture_single_newline_parser_output(
    client, auth_header, test_conversation, test_document, db
):
    """End-to-end test using real PDF parser output resolved portably."""
    conversation_id = test_conversation.id
    non_closing = NonClosingSession(db)

    fixture_path = (
        Path(__file__).resolve().parents[2] / "e2e" / "fixtures" / "citation-evidence.pdf"
    )
    parsed = PDFParserService().parse_pdf(str(fixture_path))
    page2_text = parsed["pages"][1]["content"]

    mock_hit = [
        {
            "chunk_id": 202,
            "doc_id": test_document.id,
            "page": 2,
            "text": page2_text,
            "score": 15.0,
        }
    ]

    class StreamingModel:
        async def astream(self, messages):
            prompt_content = messages[1].content
            match = re.search(
                r"\[(\d+)\]\s*\([^\)]*\)\s*\n(?:(?!\[\d+\]).)*?"
                + re.escape("All increases in the dataset size"),
                prompt_content,
                re.DOTALL,
            )
            assert match is not None, f"Supporting passage not found in prompt:\n{prompt_content}"
            source_num = match.group(1)
            yield SimpleNamespace(content=f"Data scaling yields gains across tasks [{source_num}].")

    with (
        patch("app.routers.chat.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.ChatEngine._retrieve", return_value=mock_hit),
        patch(
            "app.services.chat_engine.ChatEngine._rerank",
            new_callable=AsyncMock,
            return_value=mock_hit,
        ),
        patch(
            "app.services.chat_engine.ChatEngine.rewrite_query",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch("app.services.chat_engine.ChatEngine.model", return_value=StreamingModel()),
    ):
        response = await client.post(
            f"/api/chat/sessions/{conversation_id}/messages/stream",
            headers=auth_header,
            json={
                "content": "What happens when dataset size increases?",
                "document_ids": [test_document.id],
            },
        )

    assert response.status_code == 200
    events = [
        json.loads(block.removeprefix("data: "))
        for block in response.text.split("\n\n")
        if block.startswith("data: ")
    ]

    final_event = next(e for e in events if e.get("type") == "final")
    citations = final_event["message"]["citations"]

    assert len(citations) >= 1
    citation = citations[0]

    assert citation["page"] == 2
    assert "Figure 8" not in citation["excerpt"]
    assert "evidence" in citation
    assert citation["evidence"]["exact"] in page2_text
    assert "All increases in the dataset size" in citation["evidence"]["exact"]


@pytest.mark.asyncio
async def test_summary_fallback_omits_evidence_and_does_not_default_page_one(
    client, auth_header, test_conversation, test_document, db
):
    """Fallback section summaries must not carry verified evidence or default to page 1."""
    from app.models import Summary

    conversation_id = test_conversation.id
    non_closing = NonClosingSession(db)

    summary = Summary(
        document_id=test_document.id,
        user_id=1,
        mode="section",
        content="This is an AI generated section summary of the document overview.",
        page_number=None,
    )
    db.add(summary)
    db.commit()

    class StreamingModel:
        async def astream(self, messages):
            prompt_content = messages[1].content
            match = re.search(r"\[(\d+)\]", prompt_content)
            assert match is not None
            yield SimpleNamespace(content=f"Summary overview [{match.group(1)}].")

    with (
        patch("app.routers.chat.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.ChatEngine._retrieve", return_value=[]),
        patch(
            "app.services.chat_engine.ChatEngine._rerank", new_callable=AsyncMock, return_value=[]
        ),
        patch(
            "app.services.chat_engine.ChatEngine.rewrite_query",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch("app.services.chat_engine.ChatEngine.model", return_value=StreamingModel()),
    ):
        response = await client.post(
            f"/api/chat/sessions/{conversation_id}/messages/stream",
            headers=auth_header,
            json={
                "content": "Give me an overview.",
                "document_ids": [test_document.id],
            },
        )

    assert response.status_code == 200
    events = [
        json.loads(block.removeprefix("data: "))
        for block in response.text.split("\n\n")
        if block.startswith("data: ")
    ]

    final_event = next(e for e in events if e.get("type") == "final")
    citations = final_event["message"]["citations"]

    assert len(citations) >= 1
    citation = citations[0]

    assert citation["page"] is None
    assert "evidence" not in citation or citation["evidence"] is None


def test_single_chunk_preserves_summary_fallback_coverage():
    """One retrieved chunk must NOT suppress broad-question summary fallback."""

    class FakeSummary:
        document_id = 99
        page_number = None
        content = "High-level summary of the entire work."

    hits = [
        {
            "chunk_id": 1,
            "doc_id": 99,
            "page": 1,
            "text": "Sentence one. Sentence two. Sentence three. Sentence four.",
            "score": 10.0,
        }
    ]
    sources = build_passage_sources(
        hits=hits,
        titles={99: "Doc 99"},
        fallback_summaries=[FakeSummary()],
    )

    summary_sources = [s for s in sources if s.get("source_type") == "summary"]
    assert len(summary_sources) == 1
    assert summary_sources[0]["text"] == "High-level summary of the entire work."
    assert summary_sources[0]["evidence_text"] is None


def test_blankline_and_single_newline_passage_separation():
    """Ensure captions and body paragraphs are cleanly segregated in both formats."""
    blankline_text = (
        "Figure 8. Model scaling.\n\n"
        "Table 6. Results.\n\n"
        "All increases in dataset size result in improved performance."
    )
    spans_blank = find_passage_spans(blankline_text)
    texts_blank = [t for _, _, t in spans_blank]
    assert len(texts_blank) == 3
    assert texts_blank[0] == "Figure 8. Model scaling."
    assert texts_blank[1] == "Table 6. Results."
    assert texts_blank[2] == "All increases in dataset size result in improved performance."

    single_newline_text = (
        "Figure 8. Model scaling.\n"
        "Table 6. Results.\n"
        "All increases in dataset size result in improved performance."
    )
    spans_single = find_passage_spans(single_newline_text)
    texts_single = [t for _, _, t in spans_single]
    assert len(texts_single) == 3
    assert texts_single[0] == "Figure 8. Model scaling."
    assert texts_single[1] == "Table 6. Results."
    assert texts_single[2] == "All increases in dataset size result in improved performance."


def test_no_punctuation_long_passage_and_no_space_overflow():
    """Verify bounded splitting preserves all non-whitespace characters without dropping characters."""
    # Long text with spaces but without terminal punctuation
    words = [f"token{i}" for i in range(400)]
    long_unpunctuated = " ".join(words)
    spans_long = find_passage_spans(long_unpunctuated, min_chars=40, max_chars=300)
    assert len(spans_long) > 1
    for s, e, text_slice in spans_long:
        assert len(text_slice) <= 300
        assert text_slice == long_unpunctuated[s:e]
    # Check that all non-whitespace tokens are preserved in order
    reconstructed_tokens = [tok for _, _, t in spans_long for tok in t.split()]
    assert reconstructed_tokens == words

    # Unbroken string with no spaces exceeding max_chars
    unbroken = "ABCDEFGH" * 100  # 800 chars
    spans_unbroken = find_passage_spans(unbroken, min_chars=40, max_chars=250)
    assert len(spans_unbroken) == 4
    for s, e, text_slice in spans_unbroken:
        assert len(text_slice) <= 250
        assert text_slice == unbroken[s:e]
    # Hard-split must not drop any characters
    assert "".join(t for _, _, t in spans_unbroken) == unbroken


def test_citation_evidence_rejects_empty_or_whitespace():
    """CitationEvidence.exact must reject empty or whitespace values."""
    with pytest.raises(ValidationError):
        CitationEvidence(exact="")

    with pytest.raises(ValidationError):
        CitationEvidence(exact="   \n	  ")

    valid = CitationEvidence(exact="valid exact text")
    assert valid.exact == "valid exact text"


@pytest.mark.asyncio
async def test_citation_persistence_and_retrieval_via_messages_api(
    client, auth_header, test_conversation, test_document, db
):
    """Verify assistant message citations are persisted to the database and read back via GET endpoint."""
    conversation_id = test_conversation.id
    non_closing = NonClosingSession(db)

    mock_hit = [
        {
            "chunk_id": 101,
            "doc_id": test_document.id,
            "page": 11,
            "text": PAGE_11_CHUNK,
            "score": 12.5,
        }
    ]

    class StreamingModel:
        async def astream(self, messages):
            prompt_content = messages[1].content
            match = re.search(
                r"\[(\d+)\]\s*\([^\)]*\)\s*\n(?:(?!\[\d+\]).)*?"
                + re.escape("All increases in the dataset size"),
                prompt_content,
                re.DOTALL,
            )
            assert match is not None
            source_num = match.group(1)
            yield SimpleNamespace(content=f"Dataset scaling verified [{source_num}].")

    with (
        patch("app.routers.chat.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.SessionLocal", return_value=non_closing),
        patch("app.services.chat_engine.ChatEngine._retrieve", return_value=mock_hit),
        patch(
            "app.services.chat_engine.ChatEngine._rerank",
            new_callable=AsyncMock,
            return_value=mock_hit,
        ),
        patch(
            "app.services.chat_engine.ChatEngine.rewrite_query",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch("app.services.chat_engine.ChatEngine.model", return_value=StreamingModel()),
    ):
        post_resp = await client.post(
            f"/api/chat/sessions/{conversation_id}/messages/stream",
            headers=auth_header,
            json={
                "content": "Verify dataset scaling persistence.",
                "document_ids": [test_document.id],
            },
        )
    assert post_resp.status_code == 200

    get_resp = await client.get(
        f"/api/chat/sessions/{conversation_id}/messages",
        headers=auth_header,
    )
    assert get_resp.status_code == 200
    messages = get_resp.json()
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1
    persisted_citation = assistant_msgs[-1]["citations"][0]

    assert persisted_citation["page"] == 11
    assert "Figure 8" not in persisted_citation["excerpt"]
    assert persisted_citation["evidence"] is not None
    assert persisted_citation["evidence"]["exact"] in PAGE_11_CHUNK
    assert "All increases in the dataset size" in persisted_citation["evidence"]["exact"]


def test_schema_backward_compatibility():
    """Old citations lacking evidence field must validate cleanly into MessageResponse."""
    legacy_payload = {
        "id": 1,
        "conversation_id": 10,
        "role": "assistant",
        "content": "Answer referencing old citation [1].",
        "citations": [
            {
                "n": 1,
                "doc_id": 42,
                "doc_title": "Legacy Doc",
                "page": 5,
                "excerpt": "Legacy excerpt without evidence dictionary.",
            }
        ],
        "token_count": 10,
    }
    msg = MessageResponse.model_validate(legacy_payload)
    assert len(msg.citations) == 1
    assert msg.citations[0].evidence is None
    assert msg.citations[0].excerpt == "Legacy excerpt without evidence dictionary."

    new_payload = {
        "id": 2,
        "conversation_id": 10,
        "role": "assistant",
        "content": "Answer referencing new citation [1].",
        "citations": [
            {
                "n": 1,
                "doc_id": 42,
                "doc_title": "New Doc",
                "page": 11,
                "excerpt": "Exact text.",
                "evidence": {"exact": "Exact text."},
            }
        ],
        "token_count": 10,
    }
    msg_new = MessageResponse.model_validate(new_payload)
    assert msg_new.citations[0].evidence is not None
    assert msg_new.citations[0].evidence.exact == "Exact text."
