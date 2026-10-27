"""Chat router with RAG-powered document Q&A.

Uses the chat_engine service for RAG-powered answering with source citations.
"""
import asyncio
import json
import time
from collections import defaultdict, deque
from contextlib import suppress
from typing import Any, Coroutine, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import User, Conversation, Message, Document
from app.schemas import (
    ChatMessageCreate,
    ChatScopeUpdate,
    MessageResponse,
    ConversationResponse,
)
from app.routers.auth import get_current_user_from_token
from app.services.chat_engine import chat_engine, OllamaUnreachableError
from app.services.llm_semaphore import LLMAdmissionTimeout, LLMAdmissionUnavailable
from app.services.library_access import accessible_document_query

router = APIRouter(prefix="/api/chat", tags=["chat"])

# In-memory sliding window: max 24 messages per user per minute.
# This protects the LLM backend from hammering without being an external dep.
# A single user typing rapidly into chat can easily fire 10+ requests before
# the first one's GPU-bound response returns, which starves the queue for
# their own follow-up messages.
_CHAT_RATE_LIMIT = 24
_CHAT_RATE_WINDOW = 60.0
_chat_rate_buckets: dict[int, deque] = defaultdict(deque)


def _check_chat_rate(user_id: int) -> None:
    now = time.monotonic()
    bucket = _chat_rate_buckets[user_id]
    # Expire entries older than the window
    while bucket and bucket[0] <= now - _CHAT_RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= _CHAT_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many chat messages. Please wait a moment.",
        )
    bucket.append(now)


# How often to check whether the client is still there. FastAPI/Starlette
# never cancel a handler just because the client went away - by default an
# abandoned request keeps running to completion on the server regardless of
# whether anyone is still waiting on the response, which is exactly what
# made "Stop" in the UI a lie: the frontend could hide the spinner, but the
# LLM call kept consuming GPU time in the background. 500ms is frequent
# enough that a cancel feels immediate without adding meaningful overhead to
# a call that already takes seconds.
_DISCONNECT_POLL_SECONDS = 0.5
_STREAM_KEEPALIVE_SECONDS = 15.0


async def _run_cancellable(coro: Coroutine[Any, Any, Any], request: Request) -> Any:
    """Run `coro`, cancelling it the moment the client disconnects.

    Cancelling the asyncio Task raises CancelledError inside whatever await
    point the coroutine chain is currently suspended on - for chat_engine's
    LLM calls that's inside the httpx-based OpenAI client, which closes the
    underlying connection to Ollama/vLLM on cancellation rather than letting
    it run to completion. vLLM in particular aborts the in-flight generation
    (freeing its KV cache) when the connection closes; Ollama's behaviour on
    disconnect varies by version, but at minimum this stops US from waiting
    on it or making any further downstream calls (rerank, the final answer).

    One honest limitation: chat_engine._retrieve runs in a thread pool via
    asyncio.to_thread. Cancelling only stops US from awaiting its result -
    Python cannot forcibly kill a running thread, so if the cancel lands
    mid-retrieval that thread's (cheap, CPU-only, not GPU-bound) work finishes
    in the background rather than stopping instantly. The LLM calls, which
    are what actually matter here, are interrupted properly.
    """
    task = asyncio.ensure_future(coro)
    try:
        while not task.done():
            if await request.is_disconnected():
                task.cancel()
                break
            await asyncio.sleep(_DISCONNECT_POLL_SECONDS)
        return await task
    except asyncio.CancelledError:
        raise


def _get_user_id(user: User) -> int:
    """Eagerly materialize user.id so it survives a later session close."""
    return user.id


def _assert_documents_accessible(db: Session, user_id: int, doc_ids: List[int]) -> None:
    """Reject a chat scope naming any document outside the caller's library.

    document_ids arrive straight from the request body, and the RAG path
    downstream (services/chat_engine.py) loads documents and their extracted
    text by id with no user filter of its own. Without this check any
    authenticated user could name another user's document id in an ordinary
    chat request and get answers, citations and verbatim excerpts synthesized
    out of a private document. The router is the trust boundary where the
    caller's identity is actually known, so the constraint belongs here.

    404 rather than 403: whether a given document id exists is itself not
    something one user should be able to probe for.
    """
    if not doc_ids:
        return
    accessible = {
        d.id
        for d in accessible_document_query(db, user_id)
        .filter(Document.id.in_(doc_ids))
        .all()
    }
    missing = [i for i in doc_ids if i not in accessible]
    if missing:
        raise HTTPException(status_code=404, detail="Document not found")


@router.post("/sessions", response_model=ConversationResponse)
def create_conversation(
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Create a new chat session."""
    user_id = _get_user_id(user)
    conv = Conversation(
        user_id=user_id,
        title="New Chat",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.get("/sessions", response_model=List[ConversationResponse])
def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """List all conversations for the current user."""
    convs = db.query(Conversation).filter(
        Conversation.user_id == user.id
    ).order_by(Conversation.created_at.desc()).offset(offset).limit(limit).all()
    return convs


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse)
async def chat(
    session_id: int,
    message: ChatMessageCreate,
    request: Request,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Send a chat message and get AI response.

    The request-scoped session is closed before the LLM pipeline so a
    multi-second (or multi-minute) generation does not hold a pool
    connection idle. A fresh session writes the assistant message after.
    """
    # Eagerly materialize user.id before any other DB activity that might
    # evict the user object from the session (especially under SQLite).
    user_id = _get_user_id(user)
    _check_chat_rate(user_id)

    conv = db.query(Conversation).filter(
        Conversation.id == session_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    doc_ids = message.document_ids or (conv.document_ids or [])
    _assert_documents_accessible(db, user_id, doc_ids)
    page_scope = message.page_scope or conv.page_scope

    # Save document scope to conversation if not already set
    if message.document_ids and not conv.document_ids:
        conv.document_ids = message.document_ids
    if message.page_scope and not conv.page_scope:
        conv.page_scope = message.page_scope

    user_msg = Message(
        conversation_id=session_id,
        role="user",
        content=message.content,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)
    user_msg_id = user_msg.id

    prior = db.query(Message).filter(
        Message.conversation_id == session_id,
        Message.id < user_msg_id,
    ).order_by(Message.id.desc()).limit(6).all()
    history = [
        {"role": m.role, "content": m.content} for m in reversed(prior)
    ]

    # Release the Depends-managed connection before the long LLM await.
    # get_db's finally will close again; Session.close() is idempotent.
    db.close()

    # No documents in scope means the user is asking a general question rather
    # than one about their library, so answer it directly instead of refusing.
    # Retrieval is skipped on that path and no citations come back.
    engine_call = (
        chat_engine.answer(
            question=message.content,
            doc_ids=doc_ids,
            page_scope=page_scope,
            history=history,
            user_id=user_id,
            rag_mode=message.rag_mode,
        )
        if doc_ids
        else chat_engine.answer_general(
            question=message.content,
            history=history,
            user_id=user_id,
        )
    )
    try:
        result = await _run_cancellable(engine_call, request)
    except asyncio.CancelledError:
        # The client is gone (Stop was clicked, or the tab/request was
        # closed) - nothing to send a response to. Just stop cleanly;
        # the user's message stays in history as a real thing they sent,
        # matching what already happens on every other failure path here.
        logger.info(f"Chat message {user_msg_id} cancelled by client disconnect")
        raise HTTPException(status_code=499, detail="Request cancelled")
    except OllamaUnreachableError as e:
        # The message names the failing backend (e.g. "AI backend 'Grok' is
        # unreachable") so the user knows which one to start/fix.
        raise HTTPException(status_code=503, detail=str(e) or "AI backend is unreachable")
    except (LLMAdmissionTimeout, LLMAdmissionUnavailable) as e:
        raise HTTPException(status_code=503, detail=str(e))
    ai_content = result["content"]
    citations = result["citations"]
    token_count = result.get("token_count", 0)

    db = SessionLocal()
    try:
        ai_msg = Message(
            conversation_id=session_id,
            role="assistant",
            content=ai_content,
            citations=citations,
            token_count=token_count,
        )
        db.add(ai_msg)
        db.commit()
        db.refresh(ai_msg)
        # Detach so the response model can be read after this session closes.
        db.expunge(ai_msg)
        return ai_msg
    finally:
        db.close()


@router.post("/sessions/{session_id}/messages/stream")
async def chat_stream(
    session_id: int,
    message: ChatMessageCreate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Send a chat message and stream generated text as server-sent events.

    The user message is committed once before generation begins. The assistant
    row is committed only after generation and citation filtering finish, so
    completed streams have the same persisted shape as the legacy JSON route.
    """
    user_id = _get_user_id(user)
    _check_chat_rate(user_id)

    conv = db.query(Conversation).filter(
        Conversation.id == session_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    doc_ids = message.document_ids or (conv.document_ids or [])
    _assert_documents_accessible(db, user_id, doc_ids)
    page_scope = message.page_scope or conv.page_scope
    if message.document_ids and not conv.document_ids:
        conv.document_ids = message.document_ids
    if message.page_scope and not conv.page_scope:
        conv.page_scope = message.page_scope

    user_msg = Message(
        conversation_id=session_id,
        role="user",
        content=message.content,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)
    user_msg_id = user_msg.id

    prior = db.query(Message).filter(
        Message.conversation_id == session_id,
        Message.id < user_msg_id,
    ).order_by(Message.id.desc()).limit(6).all()
    history = [
        {"role": item.role, "content": item.content} for item in reversed(prior)
    ]
    db.close()

    async def event_stream():
        token_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)

        async def on_token(token: str) -> None:
            await token_queue.put(token)

        # Build the actual engine coroutine here so it can receive the async
        # callback without invoking the model before the response is streamed.
        if doc_ids:
            answer_task = asyncio.create_task(
                chat_engine.answer(
                    question=message.content,
                    doc_ids=doc_ids,
                    page_scope=page_scope,
                    history=history,
                    user_id=user_id,
                    rag_mode=message.rag_mode,
                    on_token=on_token,
                )
            )
        else:
            answer_task = asyncio.create_task(
                chat_engine.answer_general(
                    question=message.content,
                    history=history,
                    user_id=user_id,
                    on_token=on_token,
                )
            )

        streamed_any = False
        token_wait_task: asyncio.Task[str] | None = asyncio.create_task(
            token_queue.get()
        )
        try:
            yield _sse_event({"type": "status", "stage": "retrieving"})
            while True:
                done, _ = await asyncio.wait(
                    {answer_task, token_wait_task},
                    timeout=_STREAM_KEEPALIVE_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if token_wait_task in done:
                    token = token_wait_task.result()
                    streamed_any = True
                    yield _sse_event({"type": "token", "content": token})
                    token_wait_task = asyncio.create_task(token_queue.get())
                    continue

                if answer_task in done:
                    # Stop the pending getter before draining the queue. If
                    # queue.put woke it at the same time the answer completed,
                    # preserve the token it already took before draining the rest.
                    pending_token = None
                    if token_wait_task is not None:
                        if not token_wait_task.done():
                            cancelled = token_wait_task.cancel()
                            with suppress(asyncio.CancelledError, Exception):
                                await token_wait_task
                            if not cancelled and not token_wait_task.cancelled():
                                pending_token = token_wait_task.result()
                        elif not token_wait_task.cancelled():
                            pending_token = token_wait_task.result()
                        token_wait_task = None

                    if pending_token is not None:
                        streamed_any = True
                        yield _sse_event({"type": "token", "content": pending_token})

                    # The model can finish just after queueing several chunks.
                    # Drain them before sending final metadata.
                    while not token_queue.empty():
                        token = token_queue.get_nowait()
                        streamed_any = True
                        yield _sse_event({"type": "token", "content": token})
                    break

                # Keep intermediaries from considering slow retrieval and
                # model startup an idle connection.
                yield ": keep-alive\n\n"

            result = await answer_task
            if not streamed_any and result["content"]:
                yield _sse_event({"type": "token", "content": result["content"]})

            session = SessionLocal()
            try:
                ai_msg = Message(
                    conversation_id=session_id,
                    role="assistant",
                    content=result["content"],
                    citations=result["citations"],
                    token_count=result.get("token_count", 0),
                )
                session.add(ai_msg)
                session.commit()
                session.refresh(ai_msg)
                final_message = {
                    "id": ai_msg.id,
                    "conversation_id": ai_msg.conversation_id,
                    "role": ai_msg.role,
                    "content": ai_msg.content,
                    "citations": ai_msg.citations,
                    "token_count": ai_msg.token_count,
                }
            finally:
                session.close()
            yield _sse_event({"type": "final", "message": final_message})
        except asyncio.CancelledError:
            answer_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await answer_task
            logger.info(f"Chat stream {user_msg_id} cancelled by client disconnect")
            raise
        except (LLMAdmissionTimeout, LLMAdmissionUnavailable) as e:
            yield _sse_event({"type": "error", "detail": str(e)})
        except OllamaUnreachableError as e:
            yield _sse_event({"type": "error", "detail": str(e) or "AI backend is unreachable"})
        except Exception as e:
            logger.exception(f"Chat stream {user_msg_id} failed: {e}")
            yield _sse_event({"type": "error", "detail": str(e) or "Chat failed"})
        finally:
            if token_wait_task is not None and not token_wait_task.done():
                token_wait_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await token_wait_task
            if not answer_task.done():
                answer_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await answer_task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event: dict) -> str:
    """Encode one event using the JSON data field of server-sent events."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/sessions/{session_id}/messages", response_model=List[MessageResponse])
def get_messages(
    session_id: int,
    limit: int = Query(default=100, ge=1, le=500),  # messages are small but conversations can be very long
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get messages in a conversation, paginated."""
    conv = db.query(Conversation).filter(
        Conversation.id == session_id,
        Conversation.user_id == user.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = db.query(Message).filter(
        Message.conversation_id == session_id
    ).order_by(Message.id).offset(offset).limit(limit).all()
    return messages


@router.put("/sessions/{session_id}/scope")
def update_scope(
    session_id: int,
    scope: ChatScopeUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Update document scope for a conversation."""
    user_id = _get_user_id(user)
    conv = db.query(Conversation).filter(
        Conversation.id == session_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if scope.document_ids:
        _assert_documents_accessible(db, user_id, scope.document_ids)
        conv.document_ids = scope.document_ids
    if scope.page_scope:
        conv.page_scope = scope.page_scope
    db.commit()
    return {"status": "updated"}


@router.delete("/sessions/{session_id}")
def delete_conversation(
    session_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Delete a conversation."""
    conv = db.query(Conversation).filter(
        Conversation.id == session_id,
        Conversation.user_id == user.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"detail": "Deleted"}
