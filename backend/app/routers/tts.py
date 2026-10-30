"""TTS router for text-to-speech playback."""
import asyncio
import os
import hashlib
import uuid
import inspect
import anyio
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import User, Summary, TTSCache
from app.routers.auth import get_current_user_from_token
from app.config import settings
from app.services.llm_settings import get_default_voice
from app.services.markdown_strip import strip_markdown
from app.services.library_access import get_document_for_user
from app.tasks.tts_tasks import generate_tts as generate_tts_task, _wav_duration
from app.services.reading_audio import (
    register_producer,
    unregister_producer,
    register_reader,
    unregister_reader,
    claim_session_chunk,
    release_session_chunks,
    cleanup_aborted_or_abandoned_stream,
    is_chunk_claimed_by_session,
    heartbeat_session,
)


router = APIRouter(prefix="/api/tts", tags=["tts"])


class ReadingAudioResponse(StreamingResponse):
    def __init__(self, *args, on_finish, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_finish = on_finish

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                finish_result = self.on_finish()
                if inspect.isawaitable(finish_result):
                    await finish_result

# Mirrors tts/main.py's VOICE_MODELS registry, for the UI voice picker/lab.
# "high" quality tier sounds markedly less robotic than "medium" but is
# slower to synthesize - listed honestly so the user can judge the
# quality/speed tradeoff themselves rather than have it picked for them.
VOICE_CATALOG = [
    {
        "id": "default",
        "label": "Narrator (libritts_r)",
        "description": "Used for page and section narration. Warmer, medium quality.",
    },
    {
        "id": "host_a",
        "label": "Host A - Ryan (male, high quality)",
        "description": "Podcast dialogue voice. Slower to synthesize.",
    },
    {
        "id": "host_b",
        "label": "Host B - Lessac (female, high quality)",
        "description": "Podcast dialogue voice. Slower to synthesize.",
    },
    {
        "id": "en_US-ryan-medium",
        "label": "Ryan (male, medium quality)",
        "description": "Faster synthesis, noticeably more robotic than the high-quality tier.",
    },
    {
        "id": "en_US-lessac-medium",
        "label": "Lessac (female, medium quality)",
        "description": "Faster synthesis, noticeably more robotic than the high-quality tier.",
    },
    # Kokoro-82M: a different, more natural/expressive engine than Piper.
    # ~4-8s per short sentence on this deployment's CPU vs Piper's ~1.5-4s -
    # noticeably slower, but the naturalness gap over Piper is large enough
    # to be worth it for most reading. IDs curated from the model's graded
    # voice list (huggingface.co/hexgrad/Kokoro-82M/VOICES.md) to the
    # top-graded voice per gender/accent.
    {
        "id": "kokoro-af_heart",
        "label": "Heart (female, US, Kokoro)",
        "description": "Top-graded Kokoro voice. Natural, expressive. Slower than Piper.",
    },
    {
        "id": "kokoro-af_bella",
        "label": "Bella (female, US, Kokoro)",
        "description": "Natural, expressive. Slower than Piper.",
    },
    {
        "id": "kokoro-am_michael",
        "label": "Michael (male, US, Kokoro)",
        "description": "Natural, expressive. Slower than Piper.",
    },
    {
        "id": "kokoro-am_fenrir",
        "label": "Fenrir (male, US, Kokoro)",
        "description": "Natural, expressive. Slower than Piper.",
    },
    {
        "id": "kokoro-bf_emma",
        "label": "Emma (female, British, Kokoro)",
        "description": "Natural, expressive. Slower than Piper.",
    },
    {
        "id": "kokoro-bm_george",
        "label": "George (male, British, Kokoro)",
        "description": "Natural, expressive. Slower than Piper.",
    },
]

TEXT_MAX_CHARS = 5000


def _hash_text(text: str, user_id: int, voice: str, speed: float = 1.0) -> str:
    return hashlib.md5(f"{text}{user_id}{voice}{speed}".encode()).hexdigest()


def _cached_or_queue(
    db: Session,
    user: User,
    text: str,
    voice: str,
    doc_id: Optional[int] = None,
    summary_id: Optional[int] = None,
    speed: float = 1.0,
) -> dict:
    """Shared cache-check-then-queue logic for all play endpoints."""
    text_hash = _hash_text(text, user.id, voice, speed)

    query = db.query(TTSCache).filter(
        TTSCache.text_hash == text_hash,
        TTSCache.user_id == user.id,
    )
    if summary_id is not None:
        query = query.filter(TTSCache.summary_id == summary_id)
    cache = query.first()

    if cache and cache.audio_path and os.path.exists(cache.audio_path):
        return {
            "status": "cached",
            "audio_url": f"/api/tts/audio/{cache.id}",
            "cache_id": cache.id,
            "text_hash": text_hash,
            "duration": cache.duration,
        }

    task = generate_tts_task.delay(
        user_id=user.id,
        text=text,
        doc_id=doc_id,
        summary_id=summary_id,
        voice=voice,
        speed=speed,
    )

    return {
        "status": "generating",
        "task_id": task.id,
        "text_hash": text_hash,
    }


@router.get("/voices")
def list_voices():
    """Voice registry for the UI voice picker/lab (see tts/main.py VOICE_MODELS)."""
    return {"voices": VOICE_CATALOG}


@router.post("/release-gpu")
async def release_gpu(user: User = Depends(get_current_user_from_token)):
    """Let an administrator explicitly release the shared synthesis engine."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{settings.piper_tts_url}/release-gpu")
    except Exception as release_error:
        logger.warning(f"TTS release-gpu proxy failed: {release_error}")
    return {"status": "ok"}


class PlayTextRequest(BaseModel):
    text: str = Field(..., max_length=TEXT_MAX_CHARS)
    voice: Optional[str] = None
    # Native synthesis speed (not client-side playbackRate). Each engine has
    # its own valid range - main.py clamps per-engine (Kokoro: 0.5-2.0) and
    # the frontend applies any remainder above that as audio.playbackRate.
    speed: Optional[float] = Field(None, ge=0.1, le=4.0)


@router.post("/play/text")
def play_text(
    body: PlayTextRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Start TTS playback for an arbitrary text chunk (used by the frontend
    TTS engine for chunked gapless playback). Same cache/queue contract as
    the other play endpoints, so the existing frontend poll logic works
    unchanged. Text is usually already plain, but is markdown-stripped
    anyway since callers may pass summary-derived chunks.

    Registered before /play/{summary_id} - FastAPI matches path routes in
    registration order, and a literal "/play/text" would otherwise be
    swallowed by "/play/{summary_id}" (with "text" failing int parsing)."""
    spoken_text = strip_markdown(body.text)
    # Must pass user.id: get_default_voice() with no args returns the seed
    # default and ignores this user's Settings choice.
    resolved_voice = body.voice or get_default_voice(user.id)
    return _cached_or_queue(db, user, spoken_text, resolved_voice, speed=body.speed or 1.0)


class StreamRequest(BaseModel):
    text: str = Field(..., max_length=TEXT_MAX_CHARS)
    voice: Optional[str] = None
    speed: Optional[float] = Field(None, ge=0.1, le=4.0)
    session_id: Optional[str] = Field(None, min_length=1, max_length=128)


class ReleaseAudioRequest(BaseModel):
    session_id: Optional[str] = Field(None, min_length=1, max_length=128)
    cache_ids: Optional[list[int]] = Field(None, max_length=256)
    all_session: Optional[bool] = False
    release_all: Optional[bool] = False

    @field_validator("cache_ids")
    @classmethod
    def validate_cache_ids(cls, cache_ids_val):
        if cache_ids_val is not None:
            for item in cache_ids_val:
                if item <= 0:
                    raise ValueError("cache_ids must contain positive integers")
        return cache_ids_val


class HeartbeatAudioRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)


def _cache_dir() -> str:
    return os.path.join(settings.data_dir, "tts-cache")


def _iter_file(path: str, chunk_size: int = 65536):
    with open(path, "rb") as path_file:
        while True:
            chunk = path_file.read(chunk_size)
            if not chunk:
                break
            yield chunk


@router.post("/stream")
async def stream_tts(
    body: StreamRequest,
    request: Request,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Streaming TTS endpoint for interactive narration - bypasses Celery/Redis
    entirely so a single line of narration doesn't pay queue-and-poll latency
    (previously: browser -> API -> Redis -> Celery -> HTTP to tts -> DB row ->
    browser polling /cache-status every 400ms, 1-4s round trip and no way to
    cancel). This endpoint proxies straight through to the tts service and
    streams bytes back as they arrive.

    Uses the exact same cache namespace (_hash_text) as the Celery path so a
    line already synthesized via a podcast/summary batch job - or a previous
    call to this endpoint - is served instantly, and vice versa.

    Podcasts and summaries deliberately keep using the Celery path
    (/play/text, /play/{summary_id}) - those are long batch jobs where
    queueing is fine and retry-on-worker-death (see celery_app.py's
    acks_late config) actually matters; this endpoint has neither.
    """
    spoken_text = strip_markdown(body.text)
    resolved_voice = body.voice or get_default_voice(user.id)
    speed = body.speed or 1.0
    text_hash = _hash_text(spoken_text, user.id, resolved_voice, speed)

    cache = db.query(TTSCache).filter(
        TTSCache.text_hash == text_hash,
        TTSCache.user_id == user.id,
    ).populate_existing().with_for_update().first()

    if cache and cache.audio_path and os.path.exists(cache.audio_path):
        claim_succeeded = True
        if body.session_id:
            try:
                claim_session_chunk(user.id, body.session_id, cache.id)
            except PermissionError:
                raise HTTPException(status_code=403, detail="Session does not belong to authenticated user")
            except ValueError:
                raise HTTPException(status_code=410, detail="Reading session has ended")

        if claim_succeeded:
            cache.last_accessed = datetime.now(timezone.utc)
            register_reader(cache.id)
            try:
                db.commit()
            except BaseException:
                unregister_reader(cache.id)
                raise
            headers = {
                "X-TTS-Duration": str(cache.duration or 0),
                "X-TTS-Cache-Id": str(cache.id),
                "X-TTS-Cached": "hit",
            }
            hit_cache_id = cache.id
            hit_finished = False

            def finish_hit_stream():
                nonlocal hit_finished
                if hit_finished:
                    return
                hit_finished = True
                unregister_reader(hit_cache_id)
                cleanup_db = SessionLocal()
                try:
                    cleanup_aborted_or_abandoned_stream(cleanup_db, user.id, text_hash, hit_cache_id)
                finally:
                    cleanup_db.close()

            def _iter_hit_stream(hit_path: str):
                try:
                    with open(hit_path, "rb") as hit_file:
                        while True:
                            hit_chunk = hit_file.read(65536)
                            if not hit_chunk:
                                break
                            yield hit_chunk
                finally:
                    finish_hit_stream()

            return ReadingAudioResponse(_iter_hit_stream(cache.audio_path), media_type="audio/wav", headers=headers, on_finish=finish_hit_stream)

    # Cache miss. Reserve a TTSCache row up front (audio_path/duration/
    # size_bytes all NULL) so we have a real row id to hand back in
    # X-TTS-Cache-Id before the body has even started streaming - the id is
    # promised in the response headers, which go out before we know the
    # final size/duration. A NULL audio_path means this stub is never itself
    # mistaken for a valid cache hit by any other code path (all cache-hit
    # checks in this file test `cache.audio_path and os.path.exists(...)`)
    # until the streaming generator below fills it in.
    #
    # on_conflict_do_nothing + re-select (rather than a plain insert) handles
    # two concurrent identical requests (same user/text/voice/speed) racing
    # to create the same (text_hash, user_id) row - the loser doesn't crash
    # on the unique constraint, it just picks up the winner's row id and
    # proceeds to stream+synthesize its own copy independently (see the
    # unique temp filename below for why that's safe).
    now = datetime.now(timezone.utc)
    stmt = pg_insert(TTSCache).values(
        user_id=user.id,
        document_id=None,
        summary_id=None,
        text_hash=text_hash,
        audio_path=None,
        duration=None,
        size_bytes=None,
        created_at=now,
        last_accessed=now,
    ).on_conflict_do_nothing(index_elements=["text_hash", "user_id"])
    db.execute(stmt)

    cache = db.query(TTSCache).filter(
        TTSCache.text_hash == text_hash,
        TTSCache.user_id == user.id,
    ).populate_existing().with_for_update().first()
    cache_id = cache.id

    if body.session_id:
        try:
            claim_session_chunk(user.id, body.session_id, cache_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Session does not belong to authenticated user")
        except ValueError:
            raise HTTPException(status_code=410, detail="Reading session has ended")

    register_producer(user.id, text_hash)
    register_reader(cache_id)
    try:
        db.commit()
    except BaseException:
        unregister_reader(cache_id)
        unregister_producer(user.id, text_hash)
        raise

    output_dir = _cache_dir()
    os.makedirs(output_dir, exist_ok=True)
    producer_id = uuid.uuid4().hex
    final_path = os.path.join(output_dir, f"{cache_id}_{text_hash}_{producer_id}.wav")
    tmp_path = os.path.join(output_dir, f".{cache_id}_{text_hash}.{producer_id}.tmp")

    # Connect to the tts service and check its status BEFORE returning a
    # StreamingResponse. Once a streaming response has begun, its headers -
    # including the CORS headers - are already on the wire, so a failure part
    # way through can only present as a reset connection. In the browser that
    # surfaces as "CORS request did not succeed" with a null status code,
    # which is deeply misleading: it points at CORS configuration when the
    # real fault is upstream (a GPU OOM in Kokoro, in the case that prompted
    # this). Failing here instead returns an ordinary JSON 502 that carries
    # normal headers and an error the client can actually act on.
    client = httpx.AsyncClient(timeout=180.0)
    stream_ctx = client.stream(
        "POST",
        f"{settings.piper_tts_url}/tts",
        json={
            "text": spoken_text[:TEXT_MAX_CHARS],
            "voice": resolved_voice,
            "speed": speed,
        },
    )
    try:
        resp = await stream_ctx.__aenter__()
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            await client.aclose()
        unregister_reader(cache_id)
        unregister_producer(user.id, text_hash)
        cleanup_aborted_or_abandoned_stream(db, user.id, text_hash, cache_id, session_id=body.session_id)
        if not isinstance(exc, Exception):
            raise
        logger.error(f"tts service unreachable: {exc}")
        raise HTTPException(status_code=502, detail="TTS service is unreachable")

    if resp.status_code != 200:
        try:
            error_body = await resp.aread()
        finally:
            try:
                with anyio.CancelScope(shield=True):
                    await stream_ctx.__aexit__(None, None, None)
                    await client.aclose()
            finally:
                unregister_reader(cache_id)
                unregister_producer(user.id, text_hash)
                cleanup_aborted_or_abandoned_stream(db, user.id, text_hash, cache_id, session_id=body.session_id)
        detail = error_body[:300].decode("utf-8", "replace")
        logger.error(f"tts service returned {resp.status_code}: {detail}")
        raise HTTPException(status_code=502, detail=f"TTS synthesis failed: {detail}")

    stream_finished = False
    stream_published = False

    async def finish_stream():
        nonlocal stream_finished
        if stream_finished:
            return
        stream_finished = True
        try:
            with anyio.CancelScope(shield=True):
                await resp.aclose()
                await client.aclose()
        finally:
            unregister_reader(cache_id)
            unregister_producer(user.id, text_hash)
            cleanup_db = SessionLocal()
            try:
                cleanup_aborted_or_abandoned_stream(
                    cleanup_db, user.id, text_hash, cache_id,
                    tmp_path=tmp_path,
                    session_id=None if stream_published else body.session_id,
                )
            finally:
                cleanup_db.close()

    async def _proxy_and_cache():
        nonlocal stream_published
        # Watchdog for client disconnect. Without it, an aborted request
        # (user clicked a new line, superseding this one) would keep the
        # tts-service connection open for the rest of the synthesis: the
        # tts side only notices the closed TCP connection through its own
        # 0.3s poll, and the synthesis itself runs to completion in the
        # meantime, holding the single GPU slot and delaying the NEW
        # request the user is actually waiting on. Closing the upstream
        # connection as soon as the disconnect is seen lets the tts side
        # cancel within one poll tick.
        client_disconnected = asyncio.Event()
        watcher_done = asyncio.Event()

        async def _watch_client():
            while not watcher_done.is_set():
                try:
                    if await request.is_disconnected():
                        client_disconnected.set()
                        return
                except Exception:
                    return
                await asyncio.sleep(0.25)

        watcher = asyncio.ensure_future(_watch_client())
        try:
            wrote_complete = False
            total_bytes = 0
            read_task = None
            # aiter_bytes() is a fresh async generator on every call, so hold
            # the ONE iterator for the whole body and anext() it repeatedly -
            # re-calling aiter_bytes() each loop would re-read chunk 1 forever.
            body_iter = resp.aiter_bytes()
            try:
                with open(tmp_path, "wb") as tmp_file:
                    while True:
                        if read_task is None:
                            read_task = asyncio.ensure_future(anext(body_iter))
                        done, pending_tasks = await asyncio.wait(
                            {read_task},
                            timeout=0.25,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if not done:
                            # No upstream byte this tick - did the client
                            # go away while we were waiting?
                            if client_disconnected.is_set():
                                read_task.cancel()
                                return
                            continue
                        try:
                            chunk = read_task.result()
                        except StopAsyncIteration:
                            read_task = None
                            break
                        read_task = None
                        if not chunk:
                            continue
                        tmp_file.write(chunk)
                        total_bytes += len(chunk)
                        yield chunk
                wrote_complete = True
            finally:
                if read_task is not None and not read_task.done():
                    read_task.cancel()
                # Close the upstream tts connection no matter how we got
                # here - on a client disconnect this is what lets the tts
                # service notice and abort its in-flight synthesis.
                with anyio.CancelScope(shield=True):
                    await stream_ctx.__aexit__(None, None, None)
                    await client.aclose()
                watcher_done.set()
                watcher.cancel()
        finally:
            try:
                session_claims_chunk = True
                if body.session_id:
                    session_claims_chunk = is_chunk_claimed_by_session(body.session_id, cache_id)

                if wrote_complete and session_claims_chunk and not client_disconnected.is_set():
                    write_db = SessionLocal()
                    try:
                        row = write_db.query(TTSCache).filter(TTSCache.id == cache_id).with_for_update().first()
                        if row:
                            if row.audio_path and os.path.exists(row.audio_path):
                                os.remove(tmp_path)
                            else:
                                os.replace(tmp_path, final_path)
                                row.audio_path = final_path
                                row.size_bytes = total_bytes
                                row.duration = _wav_duration(final_path)
                            row.last_accessed = datetime.now(timezone.utc)
                            write_db.commit()
                            stream_published = True
                        elif os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    finally:
                        write_db.close()
            finally:
                await finish_stream()

    headers = {
        # Duration is genuinely unknown until the full body has been
        # received (it's derived from the WAV file's frame count), which is
        # after headers have already been sent - "0" per the documented
        # best-effort contract.
        "X-TTS-Duration": "0",
        "X-TTS-Cache-Id": str(cache_id),
        "X-TTS-Cached": "miss",
    }
    return ReadingAudioResponse(_proxy_and_cache(), media_type="audio/wav", headers=headers, on_finish=finish_stream)


@router.post("/heartbeat")
def heartbeat_reading_audio(
    body: HeartbeatAudioRequest,
    user: User = Depends(get_current_user_from_token),
):
    renewed_status = heartbeat_session(user.id, body.session_id)
    return {
        "status": "ok",
        "renewed": renewed_status,
    }


@router.post("/release")
def release_reading_audio(
    body: ReleaseAudioRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    try:
        released_count, deleted_count = release_session_chunks(
            db_session=db,
            user_id=user.id,
            session_id=body.session_id,
            cache_ids=body.cache_ids,
            release_all_session=bool(body.all_session or body.release_all),
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Session does not belong to authenticated user")
    return {
        "status": "ok",
        "released_count": released_count,
        "deleted_count": deleted_count,
    }


@router.post("/play/{summary_id}")
def play_summary(
    summary_id: int,
    voice: Optional[str] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Start TTS playback for a summary. Returns audio URL or queues generation.

    Summaries are stored as Markdown (see services/prompts.py); Piper would
    otherwise read the literal symbols aloud ("hash hash", "star star"), so
    the text is stripped to plain speakable text before hashing/synthesis.
    """
    summary = db.query(Summary).filter(
        Summary.id == summary_id,
        Summary.user_id == user.id,
        Summary.content != "",
    ).first()
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    doc = get_document_for_user(db, user.id, summary.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    spoken_text = strip_markdown(summary.content)
    resolved_voice = voice or get_default_voice(user.id)

    return _cached_or_queue(
        db, user, spoken_text, resolved_voice, doc_id=doc.id, summary_id=summary_id
    )


@router.get("/audio/{cache_id}")
def get_audio(
    cache_id: int,
    session_id: Optional[str] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Stream cached TTS audio file."""
    cache = db.query(TTSCache).filter(
        TTSCache.id == cache_id,
        TTSCache.user_id == user.id,
    ).populate_existing().with_for_update().first()

    if (
        not cache
        or not cache.audio_path
        or (cache.document_id is not None and not get_document_for_user(db, user.id, cache.document_id))
    ):
        raise HTTPException(status_code=404, detail="Audio not found")

    if not os.path.exists(cache.audio_path):
        raise HTTPException(status_code=404, detail="Audio file missing")

    if session_id:
        try:
            claim_session_chunk(user.id, session_id, cache.id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Session does not belong to authenticated user")
        except ValueError:
            raise HTTPException(status_code=410, detail="Reading session has ended")

    cache.last_accessed = datetime.now(timezone.utc)
    register_reader(cache_id)
    try:
        db.commit()
    except BaseException:
        unregister_reader(cache_id)
        raise

    target_audio_path = cache.audio_path
    audio_finished = False

    def finish_audio():
        nonlocal audio_finished
        if audio_finished:
            return
        audio_finished = True
        unregister_reader(cache_id)
        cleanup_db = SessionLocal()
        try:
            cleanup_aborted_or_abandoned_stream(cleanup_db, user.id, cache.text_hash, cache_id)
        finally:
            cleanup_db.close()

    def _iter_get_audio():
        try:
            with open(target_audio_path, "rb") as opened_audio:
                while True:
                    audio_chunk = opened_audio.read(65536)
                    if not audio_chunk:
                        break
                    yield audio_chunk
        finally:
            finish_audio()

    return ReadingAudioResponse(
        _iter_get_audio(),
        on_finish=finish_audio,
        media_type="audio/wav",
        headers={
            "Content-Length": str(os.path.getsize(target_audio_path)),
            "X-TTS-Cache-Id": str(cache.id),
        },
    )


@router.post("/play/page/{doc_id}/{page_num}")
def play_page(
    doc_id: int,
    page_num: int,
    voice: Optional[str] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Start TTS playback for a raw page's text.

    When semantic bounding boxes (from Docling) are present, extracts only
    narratable regions, skipping headers, footers, tables, captions, etc.
    Guards against empty speech when the entire page is non-narratable.
    """
    from app.models import ExtractedText
    from app.services.narration import get_narratable_text

    doc = get_document_for_user(db, user.id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document or page not found")

    page = db.query(ExtractedText).filter(
        ExtractedText.document_id == doc_id,
        ExtractedText.page_number == page_num,
    ).first()

    if not page:
        raise HTTPException(status_code=404, detail="Document or page not found")

    text_to_speak = get_narratable_text(
        content=page.content,
        narration_exclusions=page.narration_exclusions,
        layout_regions=page.layout_regions,
        bounding_boxes=page.bounding_boxes,
        fallback_content=page.content,
    )
    if not text_to_speak or not text_to_speak.strip():
        raise HTTPException(status_code=400, detail="Page contains no narratable text")

    resolved_voice = voice or get_default_voice(user.id)
    return _cached_or_queue(db, user, text_to_speak, resolved_voice, doc_id=doc.id)


@router.get("/cache/{text_hash}")
def check_cache(
    text_hash: str,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Check if TTS audio is cached/ready, for polling by the frontend."""
    cache = db.query(TTSCache).filter(
        TTSCache.text_hash == text_hash,
        TTSCache.user_id == user.id,
    ).first()

    if (
        not cache
        or not cache.audio_path
        or not os.path.exists(cache.audio_path)
        or (cache.document_id is not None and not get_document_for_user(db, user.id, cache.document_id))
    ):
        return {"status": "generating"}

    return {
        "status": "ready",
        "cache_id": cache.id,
        "audio_url": f"/api/tts/audio/{cache.id}",
        "duration": cache.duration,
    }
