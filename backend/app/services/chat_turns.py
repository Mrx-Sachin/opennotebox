"""Chat turn lifecycle service for DeepRead.

Provides shared preparation, authorization, history retrieval,
and persistence boundaries across streaming and nonstreaming chat turns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Conversation, Document, Message
from app.services.library_access import accessible_document_query

_CHAT_HISTORY_LIMIT = 6


@dataclass(frozen=True)
class PreparedChatTurn:
    session_id: int
    user_id: int
    user_message_id: int
    doc_ids: list[int]
    page_scope: dict[str, Any] | None
    history: list[dict[str, str]]


def assert_documents_accessible(
    db: Session, user_id: int, doc_ids: list[int]
) -> None:
    """Verify that all document IDs belong to the user accessible library."""
    if not doc_ids:
        return
    accessible = {
        document.id
        for document in accessible_document_query(db, user_id)
        .filter(Document.id.in_(doc_ids))
        .all()
    }
    missing_ids = [
        missing_doc_id for missing_doc_id in doc_ids if missing_doc_id not in accessible
    ]
    if missing_ids:
        raise HTTPException(status_code=404, detail="Document not found")


def get_authorized_conversation(
    db: Session, session_id: int, user_id: int
) -> Conversation:
    """Fetch conversation by ID, ensuring user ownership."""
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == session_id,
            Conversation.user_id == user_id,
        )
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def update_conversation_scope(
    db: Session,
    session_id: int,
    user_id: int,
    document_ids: list[int] | None = None,
    page_scope: dict[str, Any] | None = None,
) -> Conversation:
    """Update and persist document and page scope for an authorized conversation."""
    conversation = get_authorized_conversation(db, session_id=session_id, user_id=user_id)
    if document_ids:
        assert_documents_accessible(db, user_id, document_ids)
        conversation.document_ids = document_ids
    if page_scope:
        conversation.page_scope = page_scope
    db.commit()
    return conversation


def _load_bounded_history(
    db: Session,
    session_id: int,
    before_message_id: int,
) -> list[dict[str, str]]:
    """Retrieve prior conversation messages chronologically up to fixed bound."""
    prior_messages = (
        db.query(Message)
        .filter(
            Message.conversation_id == session_id,
            Message.id < before_message_id,
        )
        .order_by(Message.id.desc())
        .limit(_CHAT_HISTORY_LIMIT)
        .all()
    )
    return [
        {"role": prior_message.role, "content": prior_message.content}
        for prior_message in reversed(prior_messages)
    ]


def prepare_chat_turn(
    db: Session,
    session_id: int,
    user_id: int,
    content: str,
    document_ids: list[int] | None = None,
    page_scope: dict[str, Any] | None = None,
) -> PreparedChatTurn:
    """Prepare a chat turn by validating access, persisting user message, and loading history."""
    conversation = get_authorized_conversation(db, session_id=session_id, user_id=user_id)

    doc_ids = document_ids or (conversation.document_ids or [])
    assert_documents_accessible(db, user_id, doc_ids)
    effective_page_scope = page_scope or conversation.page_scope

    if document_ids and not conversation.document_ids:
        conversation.document_ids = document_ids
    if page_scope and not conversation.page_scope:
        conversation.page_scope = page_scope

    user_msg = Message(
        conversation_id=session_id,
        role="user",
        content=content,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    history = _load_bounded_history(
        db=db,
        session_id=session_id,
        before_message_id=user_msg.id,
    )

    return PreparedChatTurn(
        session_id=session_id,
        user_id=user_id,
        user_message_id=user_msg.id,
        doc_ids=doc_ids,
        page_scope=effective_page_scope,
        history=history,
    )


def save_assistant_message(
    session_id: int,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    token_count: int = 0,
    session_factory: Callable[[], Session] | None = None,
) -> Message:
    """Persist an assistant message in a dedicated database session and expunge it."""
    factory = session_factory or SessionLocal
    session = factory()
    try:
        ai_msg = Message(
            conversation_id=session_id,
            role="assistant",
            content=content,
            citations=citations,
            token_count=token_count,
        )
        session.add(ai_msg)
        session.commit()
        session.refresh(ai_msg)
        session.expunge(ai_msg)
        return ai_msg
    finally:
        session.close()
