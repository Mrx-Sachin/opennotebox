"""Tests for chat router endpoints.

Covers: create/list/get/delete conversations, message sending,
scope updates, ownership enforcement, and unauthenticated access.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Conversation, Message


class TestCreateConversation:
    """POST /api/chat/sessions"""

    async def test_create_success(self, client, auth_header):
        resp = await client.post("/api/chat/sessions", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Chat"
        assert data["user_id"] is not None

    async def test_create_unauthenticated(self, client):
        resp = await client.post("/api/chat/sessions")
        assert resp.status_code == 401

    async def test_create_multiple(self, client, auth_header):
        r1 = await client.post("/api/chat/sessions", headers=auth_header)
        r2 = await client.post("/api/chat/sessions", headers=auth_header)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["id"] != r2.json()["id"]


class TestListConversations:
    """GET /api/chat/sessions"""

    async def test_list_with_conversations(self, client, auth_header, test_conversation):
        resp = await client.get("/api/chat/sessions", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert any(c["id"] == test_conversation.id for c in data)

    async def test_list_unauthenticated(self, client):
        resp = await client.get("/api/chat/sessions")
        assert resp.status_code == 401

    async def test_list_ordered_by_newest(self, client, auth_header):
        for _ in range(3):
            await client.post("/api/chat/sessions", headers=auth_header)
        resp = await client.get("/api/chat/sessions", headers=auth_header)
        data = resp.json()
        ids = [c["id"] for c in data]
        assert ids == sorted(ids, reverse=True)


class TestGetMessages:
    """GET /api/chat/sessions/{session_id}/messages"""

    async def test_get_messages_success(self, client, auth_header, test_conversation):
        resp = await client.get(
            f"/api/chat/sessions/{test_conversation.id}/messages",
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        assert data[0]["role"] == "user"
        assert data[1]["role"] == "assistant"

    async def test_get_messages_nonexistent(self, client, auth_header):
        resp = await client.get(
            "/api/chat/sessions/99999/messages", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_get_messages_other_user(self, client, auth_header, db, second_user):
        conv = Conversation(user_id=second_user.id, title="Secret Chat")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        resp = await client.get(
            f"/api/chat/sessions/{conv.id}/messages", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_get_messages_unauthenticated(self, client):
        resp = await client.get("/api/chat/sessions/1/messages")
        assert resp.status_code == 401


class TestChatMessage:
    """POST /api/chat/sessions/{session_id}/messages"""

    @patch("app.routers.chat.SessionLocal", lambda: None)
    @patch("app.services.chat_engine.ChatEngine.answer_general", new_callable=AsyncMock)
    async def test_chat_general_success(self, mock_answer, client, auth_header, test_conversation, db):
        # Patch SessionLocal to return our test session so the endpoint's
        # post-LLM db.write uses the rolled-back test session.
        with patch("app.routers.chat.SessionLocal", return_value=db):
            mock_answer.return_value = {
                "content": "This is a test AI response.",
                "citations": [],
                "token_count": 15,
            }
            resp = await client.post(
                f"/api/chat/sessions/{test_conversation.id}/messages",
                headers=auth_header,
                json={"content": "Hello, what is this about?"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["role"] == "assistant"
            assert "test AI response" in data["content"]

    @patch("app.routers.chat.SessionLocal", lambda: None)
    @patch("app.services.chat_engine.ChatEngine.answer", new_callable=AsyncMock)
    async def test_chat_with_documents(self, mock_answer, client, auth_header, test_conversation, test_document, db):
        with patch("app.routers.chat.SessionLocal", return_value=db):
            mock_answer.return_value = {
                "content": "Based on the document, the answer is 42.",
                "citations": [{"chunk_id": 1, "page": 1}],
                "token_count": 20,
            }
            resp = await client.post(
                f"/api/chat/sessions/{test_conversation.id}/messages",
                headers=auth_header,
                json={
                    "content": "What is the answer?",
                    "document_ids": [test_document.id],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["citations"] is not None

    async def test_chat_nonexistent_session(self, client, auth_header):
        resp = await client.post(
            "/api/chat/sessions/99999/messages",
            headers=auth_header,
            json={"content": "test"},
        )
        assert resp.status_code == 404

    async def test_chat_other_user_session(self, client, auth_header, db, second_user):
        conv = Conversation(user_id=second_user.id, title="Private")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        resp = await client.post(
            f"/api/chat/sessions/{conv.id}/messages",
            headers=auth_header,
            json={"content": "test"},
        )
        assert resp.status_code == 404

    async def test_chat_unauthenticated(self, client):
        resp = await client.post(
            "/api/chat/sessions/1/messages",
            json={"content": "test"},
        )
        assert resp.status_code == 401

    @patch("app.routers.chat.SessionLocal", lambda: None)
    async def test_chat_general_unreachable_names_backend(
        self, client, auth_header, test_conversation, db
    ):
        """A dead backend must 503 with a message naming THAT backend, not
        a generic "AI backend is unreachable" the user cannot act on."""
        from app.services.chat_engine import OllamaUnreachableError

        with patch("app.routers.chat.SessionLocal", return_value=db):
            with patch(
                "app.services.chat_engine.ChatEngine.answer_general",
                new_callable=AsyncMock,
                side_effect=OllamaUnreachableError("AI backend 'Grok' is unreachable"),
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{test_conversation.id}/messages",
                    headers=auth_header,
                    json={"content": "hii"},
                )
        assert resp.status_code == 503
        assert "Grok" in resp.json()["detail"]

    @patch("app.routers.chat.SessionLocal", lambda: None)
    async def test_chat_general_unreachable_generic_fallback(
        self, client, auth_header, test_conversation, db
    ):
        """The engine's bare message (no backend name resolvable) still
        surfaces as a 503 with the generic text, never an empty detail."""
        from app.services.chat_engine import OllamaUnreachableError

        with patch("app.routers.chat.SessionLocal", return_value=db):
            with patch(
                "app.services.chat_engine.ChatEngine.answer_general",
                new_callable=AsyncMock,
                side_effect=OllamaUnreachableError("AI backend is unreachable"),
            ):
                resp = await client.post(
                    f"/api/chat/sessions/{test_conversation.id}/messages",
                    headers=auth_header,
                    json={"content": "hii"},
                )
        assert resp.status_code == 503
        assert resp.json()["detail"] == "AI backend is unreachable"

    async def test_unreachable_message_names_active_backend(self, db, test_user):
        """_unreachable_message resolves the user's active backend name,
        and degrades to the generic message when lookup fails."""
        from app.services.chat_engine import _unreachable_message

        registry = [
            {"name": "dead-bridge", "base_url": "http://x:1/v1", "model": "m", "api_key": None}
        ]
        with (
            patch("app.services.llm_settings._load_backends", return_value=registry),
            patch(
                "app.services.llm_settings._load_user_prefs",
                return_value={"active": "dead-bridge", "model_overrides": {}},
            ),
        ):
            assert _unreachable_message(test_user.id) == "AI backend 'dead-bridge' is unreachable"

        with patch("app.services.llm_settings.get_active_llm", side_effect=RuntimeError("boom")):
            assert _unreachable_message(test_user.id) == "AI backend is unreachable"

    async def test_chat_empty_content(self, client, auth_header, test_conversation, db):
        with patch("app.routers.chat.SessionLocal", return_value=db):
            with patch("app.services.chat_engine.ChatEngine.answer_general", new_callable=AsyncMock) as mock_answer:
                mock_answer.return_value = {
                    "content": "Response to empty message.",
                    "citations": [],
                    "token_count": 5,
                }
                resp = await client.post(
                    f"/api/chat/sessions/{test_conversation.id}/messages",
                    headers=auth_header,
                    json={"content": ""},
                )
                # Empty content should either be accepted (200) or rejected by validation (422)
                assert resp.status_code in (200, 422)


class TestChatStream:
    """POST /api/chat/sessions/{session_id}/messages/stream."""

    @pytest.mark.parametrize("emit_token", [False, True])
    async def test_completion_does_not_wait_for_keepalive_timeout(
        self,
        client,
        auth_header,
        test_conversation,
        db,
        monkeypatch,
        emit_token,
    ):
        monkeypatch.setattr("app.routers.chat._STREAM_KEEPALIVE_SECONDS", 0.5)

        async def delayed_answer(*, question, history, user_id, on_token):
            if emit_token:
                await on_token("Last token.")
            await asyncio.sleep(0.01)
            content = "Last token." if emit_token else "No model tokens."
            return {"content": content, "citations": [], "token_count": 3}

        with patch("app.routers.chat.SessionLocal", return_value=db):
            with patch(
                "app.routers.chat.chat_engine.answer_general",
                new_callable=AsyncMock,
                side_effect=delayed_answer,
            ):
                started = time.perf_counter()
                response = await client.post(
                    f"/api/chat/sessions/{test_conversation.id}/messages/stream",
                    headers=auth_header,
                    json={"content": "Finish promptly."},
                )
                elapsed = time.perf_counter() - started

        assert response.status_code == 200
        assert elapsed < 0.25
        events = [
            json.loads(block.removeprefix("data: "))
            for block in response.text.split("\n\n")
            if block.startswith("data: ")
        ]
        assert events[-1]["type"] == "final"
        assert events[-1]["message"]["content"] == (
            "Last token." if emit_token else "No model tokens."
        )

    async def test_stream_emits_tokens_and_persists_once(
        self, client, auth_header, test_conversation, db
    ):
        conversation_id = test_conversation.id

        async def answer_with_tokens(*, question, history, user_id, on_token):
            await on_token("A streamed ")
            await on_token("answer.")
            return {
                "content": "A streamed answer.",
                "citations": [],
                "token_count": 3,
            }

        with patch("app.routers.chat.SessionLocal", return_value=db):
            with patch(
                "app.routers.chat.chat_engine.answer_general",
                new_callable=AsyncMock,
                side_effect=answer_with_tokens,
            ):
                response = await client.post(
                    f"/api/chat/sessions/{conversation_id}/messages/stream",
                    headers=auth_header,
                    json={"content": "Please stream this answer."},
                )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(block.removeprefix("data: "))
            for block in response.text.split("\n\n")
            if block.startswith("data: ")
        ]
        assert [event["type"] for event in events] == [
            "status",
            "token",
            "token",
            "final",
        ]
        assert "".join(
            event["content"] for event in events if event["type"] == "token"
        ) == "A streamed answer."
        assert events[-1]["message"]["content"] == "A streamed answer."

        messages = await client.get(
            f"/api/chat/sessions/{conversation_id}/messages",
            headers=auth_header,
        )
        sent = [
            row for row in messages.json()
            if row["role"] == "user" and row["content"] == "Please stream this answer."
        ]
        saved_answers = [
            row for row in messages.json()
            if row["role"] == "assistant" and row["content"] == "A streamed answer."
        ]
        assert len(sent) == 1
        assert len(saved_answers) == 1

    async def test_stream_drains_burst_tokens_before_final(
        self, client, auth_header, test_conversation, db
    ):
        conversation_id = test_conversation.id
        tokens = [f"chunk-{index} " for index in range(20)]

        async def answer_with_burst(*, question, history, user_id, on_token):
            for token in tokens:
                await on_token(token)
                await asyncio.sleep(0)
            return {
                "content": "".join(tokens),
                "citations": [],
                "token_count": len(tokens),
            }

        with patch("app.routers.chat.SessionLocal", return_value=db):
            with patch(
                "app.routers.chat.chat_engine.answer_general",
                new_callable=AsyncMock,
                side_effect=answer_with_burst,
            ):
                response = await client.post(
                    f"/api/chat/sessions/{conversation_id}/messages/stream",
                    headers=auth_header,
                    json={"content": "Stream every chunk."},
                )

        events = [
            json.loads(block.removeprefix("data: "))
            for block in response.text.split("\n\n")
            if block.startswith("data: ")
        ]
        emitted_tokens = [
            event["content"] for event in events if event["type"] == "token"
        ]
        assert response.status_code == 200
        assert emitted_tokens == tokens
        assert events[-1]["type"] == "final"
        assert events[-1]["message"]["content"] == "".join(tokens)

    async def test_stream_rejects_another_users_document(
        self, client, auth_header, test_conversation, second_document
    ):
        response = await client.post(
            f"/api/chat/sessions/{test_conversation.id}/messages/stream",
            headers=auth_header,
            json={"content": "Read this private document.", "document_ids": [second_document.id]},
        )
        assert response.status_code == 404

    async def test_stream_saves_final_rag_citations(
        self, client, auth_header, test_conversation, test_document, db
    ):
        conversation_id = test_conversation.id
        citations = [{
            "n": 1,
            "doc_id": test_document.id,
            "doc_title": test_document.title,
            "page": 1,
            "excerpt": "The test page.",
        }]

        async def answer_with_citation(*, question, doc_ids, page_scope, history,
                                       user_id, rag_mode, on_token):
            await on_token("The source says [1].")
            return {
                "content": "The source says [1].",
                "citations": citations,
                "token_count": 5,
            }

        with patch("app.routers.chat.SessionLocal", return_value=db):
            with patch(
                "app.routers.chat.chat_engine.answer",
                new_callable=AsyncMock,
                side_effect=answer_with_citation,
            ):
                response = await client.post(
                    f"/api/chat/sessions/{conversation_id}/messages/stream",
                    headers=auth_header,
                    json={
                        "content": "What does the source say?",
                        "document_ids": [test_document.id],
                    },
                )

        events = [
            json.loads(block.removeprefix("data: "))
            for block in response.text.split("\n\n")
            if block.startswith("data: ")
        ]
        assert response.status_code == 200
        assert events[-1]["message"]["citations"] == citations

        messages = await client.get(
            f"/api/chat/sessions/{conversation_id}/messages",
            headers=auth_header,
        )
        saved_answer = next(
            row for row in messages.json()
            if row["role"] == "assistant" and row["content"] == "The source says [1]."
        )
        assert saved_answer["citations"] == citations

    async def test_stream_requires_auth(self, client, test_conversation):
        response = await client.post(
            f"/api/chat/sessions/{test_conversation.id}/messages/stream",
            json={"content": "Please authenticate."},
        )
        assert response.status_code == 401


class TestDeleteConversation:
    """DELETE /api/chat/sessions/{session_id}"""

    async def test_delete_success(self, client, auth_header, test_conversation):
        conv_id = test_conversation.id
        resp = await client.delete(
            f"/api/chat/sessions/{conv_id}", headers=auth_header
        )
        assert resp.status_code == 200
        resp = await client.get(
            f"/api/chat/sessions/{conv_id}/messages", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_delete_other_user(self, client, auth_header, db, second_user):
        conv = Conversation(user_id=second_user.id, title="Other")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        resp = await client.delete(
            f"/api/chat/sessions/{conv.id}", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_delete_nonexistent(self, client, auth_header):
        resp = await client.delete("/api/chat/sessions/99999", headers=auth_header)
        assert resp.status_code == 404

    async def test_delete_cascades_messages(self, client, auth_header, db, test_user):
        conv = Conversation(user_id=test_user.id, title="To Delete")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        msg = Message(
            conversation_id=conv.id, role="user", content="test"
        )
        db.add(msg)
        db.commit()
        resp = await client.delete(
            f"/api/chat/sessions/{conv.id}", headers=auth_header
        )
        assert resp.status_code == 200
        resp = await client.get(
            f"/api/chat/sessions/{conv.id}/messages", headers=auth_header
        )
        assert resp.status_code == 404


class TestUpdateScope:
    """PUT /api/chat/sessions/{session_id}/scope"""

    async def test_update_scope_success(self, client, auth_header, test_conversation, test_document):
        resp = await client.put(
            f"/api/chat/sessions/{test_conversation.id}/scope",
            headers=auth_header,
            json={
                "document_ids": [test_document.id],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_update_scope_other_user_doc(
        self, client, auth_header, test_conversation, second_document
    ):
        resp = await client.put(
            f"/api/chat/sessions/{test_conversation.id}/scope",
            headers=auth_header,
            json={
                "document_ids": [second_document.id],
            },
        )
        assert resp.status_code == 404

    async def test_update_scope_nonexistent(self, client, auth_header):
        resp = await client.put(
            "/api/chat/sessions/99999/scope",
            headers=auth_header,
            json={"content": ""},
        )
        assert resp.status_code == 404
