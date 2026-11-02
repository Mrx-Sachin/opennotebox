"""Unit and integration tests for podcast interruption, transcription, and audio synthesis."""
import io
import json
import math
import os
from pathlib import Path
import struct
from unittest.mock import AsyncMock, MagicMock, patch
import wave

from fastapi import HTTPException
from langchain_core.messages import AIMessage
import pytest
import httpx

from app.config import settings
from app.models import Document, Podcast, User
from app.services.auth_service import create_access_token, get_password_hash
from app.services.podcast_interaction import (
    generate_interruption_dialogue,
    resolve_active_exchange,
    retrieve_podcast_context,
    synthesize_interruption_audio,
    transcribe_question,
)


def _header(user_id: int) -> dict[str, str]:
    token = create_access_token({"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}


def _create_user(db, username: str) -> User:
    user = User(
        username=username,
        password_hash=get_password_hash("testpass123"),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_doc(db, user_id: int, title: str = "Interruption Doc") -> Document:
    doc = Document(
        user_id=user_id,
        title=title,
        format="pdf",
        file_path="/tmp/test_interrupt.pdf",
        file_size=1024,
        processing_status="completed",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _create_podcast(db, user_id: int, document_id: int, **kwargs) -> Podcast:
    podcast = Podcast(
        user_id=user_id,
        document_id=document_id,
        scope=kwargs.pop("scope", "full"),
        status=kwargs.pop("status", "completed"),
        length=kwargs.pop("length", "medium"),
        duration=kwargs.pop("duration", 180.0),
        **kwargs,
    )
    db.add(podcast)
    db.commit()
    db.refresh(podcast)
    return podcast


def _generate_synthetic_pcm_wav(
    duration_seconds: float = 1.0,
    sample_rate: int = 24000,
    frequency: float = 440.0,
) -> bytes:
    """Generate in-memory WAV bytes for 16-bit mono PCM."""
    num_frames = int(duration_seconds * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        samples = []
        for index in range(num_frames):
            value = int(32767.0 * 0.2 * math.sin(2.0 * math.pi * frequency * (index / sample_rate)))
            samples.append(struct.pack("<h", value))
        wav_file.writeframes(b"".join(samples))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 1. resolve_active_exchange unit tests
# ---------------------------------------------------------------------------

def test_resolve_active_exchange_timestamps_exact_hit():
    """Verify exact hit inside a dialogue line with start_time and end_time."""
    transcript = {
        "lines": [
            {"speaker": "A", "text": "Line zero", "start_time": 0.0, "end_time": 2.0},
            {"speaker": "B", "text": "Line one", "start_time": 2.3, "end_time": 4.5},
            {"speaker": "A", "text": "Line two", "start_time": 4.8, "end_time": 6.0},
        ]
    }

    # At 1.0s: inside line 0
    curr, nxt, idx = resolve_active_exchange(transcript, 1.0)
    assert idx == 0
    assert curr == transcript["lines"][0]
    assert nxt == transcript["lines"][1]

    # At 3.0s: inside line 1
    curr, nxt, idx = resolve_active_exchange(transcript, 3.0)
    assert idx == 1
    assert curr == transcript["lines"][1]
    assert nxt == transcript["lines"][2]

    # At 5.5s: inside line 2 (last line)
    curr, nxt, idx = resolve_active_exchange(transcript, 5.5)
    assert idx == 2
    assert curr == transcript["lines"][2]
    assert nxt is None


def test_resolve_active_exchange_timestamps_silence_gap():
    """Verify silence gap between line i and line i+1 resolves to (line_i, line_{i+1}, i)."""
    transcript = {
        "lines": [
            {"speaker": "A", "text": "Line zero", "start_time": 0.0, "end_time": 2.0},
            {"speaker": "B", "text": "Line one", "start_time": 2.3, "end_time": 4.5},
            {"speaker": "A", "text": "Line two", "start_time": 4.8, "end_time": 6.0},
        ]
    }

    # At 2.15s: in silence gap between 2.0 and 2.3
    curr, nxt, idx = resolve_active_exchange(transcript, 2.15)
    assert idx == 0
    assert curr == transcript["lines"][0]
    assert nxt == transcript["lines"][1]

    # At 4.65s: in silence gap between 4.5 and 4.8
    curr, nxt, idx = resolve_active_exchange(transcript, 4.65)
    assert idx == 1
    assert curr == transcript["lines"][1]
    assert nxt == transcript["lines"][2]


def test_resolve_active_exchange_timestamps_boundary_edges():
    """Verify behavior before line 0 and after the final line."""
    transcript = {
        "lines": [
            {"speaker": "A", "text": "Line zero", "start_time": 1.0, "end_time": 3.0},
            {"speaker": "B", "text": "Line one", "start_time": 3.3, "end_time": 5.0},
        ]
    }

    # Before start (e.g. 0.5s < 1.0s)
    curr, nxt, idx = resolve_active_exchange(transcript, 0.5)
    assert curr is None
    assert nxt == transcript["lines"][0]
    assert idx == 0

    # After end (e.g. 7.0s > 5.0s)
    curr, nxt, idx = resolve_active_exchange(transcript, 7.0)
    assert curr == transcript["lines"][1]
    assert nxt is None
    assert idx == 1


def test_resolve_active_exchange_explicit_line_index():
    """Verify explicit line_index override takes precedence over timestamp matching."""
    transcript = {
        "lines": [
            {"speaker": "A", "text": "Line zero", "start_time": 0.0, "end_time": 2.0},
            {"speaker": "B", "text": "Line one", "start_time": 2.3, "end_time": 4.5},
            {"speaker": "A", "text": "Line two", "start_time": 4.8, "end_time": 6.0},
        ]
    }

    # Explicitly pick line 1 even if current_time is at 0.5s
    curr, nxt, idx = resolve_active_exchange(transcript, 0.5, line_index=1)
    assert idx == 1
    assert curr == transcript["lines"][1]
    assert nxt == transcript["lines"][2]

    # Explicitly pick last line
    curr, nxt, idx = resolve_active_exchange(transcript, 0.5, line_index=2)
    assert idx == 2
    assert curr == transcript["lines"][2]
    assert nxt is None

    # Invalid line_index falls back to current_time resolution
    curr, nxt, idx = resolve_active_exchange(transcript, 5.5, line_index=99)
    assert idx == 2
    assert curr == transcript["lines"][2]


def test_resolve_active_exchange_legacy_proportional():
    """Verify character-weighted estimated timestamps for legacy transcripts without timings."""
    text0 = "A" * 50
    text1 = "B" * 100
    text2 = "C" * 50
    transcript = {
        "duration": 20.0,
        "lines": [
            {"speaker": "A", "text": text0},  # 0.0s to 5.0s
            {"speaker": "B", "text": text1},  # 5.0s to 15.0s
            {"speaker": "A", "text": text2},  # 15.0s to 20.0s
        ]
    }

    # Before start (< 0)
    curr, nxt, idx = resolve_active_exchange(transcript, -1.0)
    assert curr is None
    assert nxt == transcript["lines"][0]
    assert idx == 0

    # Inside first line: 2.5s
    curr, nxt, idx = resolve_active_exchange(transcript, 2.5)
    assert idx == 0
    assert curr == transcript["lines"][0]
    assert nxt == transcript["lines"][1]

    # Inside second line: 10.0s
    curr, nxt, idx = resolve_active_exchange(transcript, 10.0)
    assert idx == 1
    assert curr == transcript["lines"][1]
    assert nxt == transcript["lines"][2]

    # Inside third line: 18.0s
    curr, nxt, idx = resolve_active_exchange(transcript, 18.0)
    assert idx == 2
    assert curr == transcript["lines"][2]
    assert nxt is None

    # After end: 25.0s
    curr, nxt, idx = resolve_active_exchange(transcript, 25.0)
    assert idx == 2
    assert curr == transcript["lines"][2]
    assert nxt is None


def test_resolve_active_exchange_empty():
    """Verify empty or missing transcript handles cleanly."""
    assert resolve_active_exchange(None, 0.0) == (None, None, 0)
    assert resolve_active_exchange({}, 0.0) == (None, None, 0)
    assert resolve_active_exchange({"lines": []}, 0.0) == (None, None, 0)


# ---------------------------------------------------------------------------
# 2. Context retrieval tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_podcast_context():
    """Verify document search hits format into context and citations without blocking LLM calls."""
    mock_db = MagicMock()
    mock_hits = [
        {"chunk_id": 101, "doc_id": 1, "page": 1, "text": "First passage text.", "score": 0.8},
        {"chunk_id": 102, "doc_id": 1, "page": 2, "text": "Second passage text.", "score": 0.7},
    ]

    with patch("app.services.retrieval.search", return_value=mock_hits) as mock_search:
        context, citations = await retrieve_podcast_context(mock_db, doc_id=1, question="Test question")
        mock_search.assert_called_once()
        assert "[page 1] First passage text." in context
        assert "[page 2] Second passage text." in context
        assert len(citations) == 2
        assert citations[0]["chunk_id"] == 101
        assert citations[0]["page"] == 1


# ---------------------------------------------------------------------------
# 3. Interruption dialogue generation tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_interruption_dialogue():
    """Verify LLM prompt rendering and structured JSON extraction."""
    mock_llm_response = AIMessage(
        content=json.dumps({
            "lines": [
                {"speaker": "A", "text": "Great listener question!"},
                {"speaker": "B", "text": "Indeed, here is the explanation."},
            ]
        })
    )

    with patch("app.services.podcast_interaction.get_active_llm", return_value={
        "base_url": "http://mock-llm", "model": "mock-model", "api_key": "test"
    }), patch("langchain_openai.ChatOpenAI.ainvoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_llm_response

        lines = await generate_interruption_dialogue(
            document_title="Sample Title",
            user_question="Why is the sky blue?",
            context="[page 1] Rayleigh scattering",
            current_line={"speaker": "A", "text": "As we were saying..."},
            next_line={"speaker": "B", "text": "Moving along..."},
        )

        assert len(lines) == 2
        assert lines[0] == {"speaker": "A", "text": "Great listener question!"}
        assert lines[1] == {"speaker": "B", "text": "Indeed, here is the explanation."}


# ---------------------------------------------------------------------------
# 4. Audio synthesis tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesize_interruption_audio(tmp_path: Path):
    """Verify HTTP TTS calls and WAV stitching producing audio file."""
    synthetic_wav = _generate_synthetic_pcm_wav(duration_seconds=1.0)
    lines = [
        {"speaker": "A", "text": "First answer line."},
        {"speaker": "B", "text": "Second answer line."},
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = synthetic_wav

    async def mock_post(self, *args, **kwargs):
        return mock_resp

    with patch("app.config.settings.data_dir", str(tmp_path)),          patch("app.services.podcast_interaction.httpx.AsyncClient.post", mock_post):
        out_path, duration = await synthesize_interruption_audio(lines, "interrupt_123")
        assert os.path.exists(out_path)
        assert duration > 0.0
        assert out_path.endswith("interrupt_123.wav")


# ---------------------------------------------------------------------------
# 5. Transcription tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transcribe_question_with_meta_key():
    """Verify ASR transcription with configured Meta API key."""
    audio_data = b"RIFFfakeaudio"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"transcript": "What are transformer models?"}

    async def mock_post(self, *args, **kwargs):
        return mock_resp

    with patch.object(settings, "meta_api_key", "meta-secret-key"),          patch("app.services.podcast_interaction.httpx.AsyncClient.post", mock_post):
        transcript = await transcribe_question(audio_data, "q.wav")
        assert transcript == "What are transformer models?"


@pytest.mark.asyncio
async def test_transcribe_question_without_meta_key():
    """Verify 503 HTTPException when Meta API key is not configured."""
    with patch.object(settings, "meta_api_key", ""),          patch.dict(os.environ, {"MODEL_API_KEY": "", "META_API_KEY": ""}):
        with pytest.raises(HTTPException) as exc_info:
            await transcribe_question(b"audio", "q.wav")
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# 6. End-to-end API router tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_interrupt_endpoint_success(client, db, test_user, test_document, auth_header, tmp_path: Path):
    """Verify POST /api/podcasts/{id}/interrupt orchestrates dialogue, audio, and response metadata."""
    synthetic_wav = _generate_synthetic_pcm_wav(duration_seconds=1.0)

    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="completed",
        transcript={
            "version": 2,
            "title": "Quantum Physics 101",
            "lines": [
                {"speaker": "A", "text": "Welcome to physics.", "start_time": 0.0, "end_time": 2.0, "duration": 2.0},
                {"speaker": "B", "text": "Let us talk about atoms.", "start_time": 2.3, "end_time": 5.0, "duration": 2.7},
            ],
        },
    )

    mock_llm_response = AIMessage(
        content=json.dumps({
            "lines": [
                {"speaker": "A", "text": "That is a great question on photons!"},
                {"speaker": "B", "text": "Photons carry electromagnetic force. Back to atoms."},
            ]
        })
    )

    async def mock_synth(lines, interrupt_id):
        out_dir = os.path.join(str(tmp_path), "podcast_interrupts")
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"{interrupt_id}.wav")
        with open(out_file, "wb") as f:
            f.write(synthetic_wav)
        return out_file, 2.5

    with patch("app.config.settings.data_dir", str(tmp_path)),          patch("app.services.retrieval.search", return_value=[{"chunk_id": 1, "doc_id": test_document.id, "page": 1, "text": "Photons are quanta."}]),          patch("app.services.podcast_interaction.get_active_llm", return_value={"base_url": "http://llm", "model": "m", "api_key": "k"}),          patch("langchain_openai.ChatOpenAI.ainvoke", new_callable=AsyncMock, return_value=mock_llm_response),          patch("app.services.podcast_interaction.synthesize_interruption_audio", side_effect=mock_synth):

        res = await client.post(
            f"/api/podcasts/{podcast.id}/interrupt",
            headers=auth_header,
            json={"question": "What is a photon?", "current_time": 1.5},
        )
        assert res.status_code == 200
        data = res.json()
        assert "interrupt_id" in data
        assert data["audio_url"] == f"/api/podcasts/{podcast.id}/interrupts/{data['interrupt_id']}/audio"
        assert len(data["lines"]) == 2
        assert data["lines"][0]["speaker"] == "A"
        assert data["current_line"]["text"] == "Welcome to physics."
        assert data["next_line"]["text"] == "Let us talk about atoms."
        assert data["resume_time"] == 0.0
        assert len(data["citations"]) == 1

        # Now test the GET audio endpoint for this generated interrupt!
        audio_res = await client.get(data["audio_url"], headers=auth_header)
        assert audio_res.status_code == 200
        assert audio_res.headers["content-type"] == "audio/wav"
        assert len(audio_res.content) > 0


@pytest.mark.asyncio
async def test_post_interrupt_endpoint_not_completed(client, db, test_user, test_document, auth_header):
    """Verify 400 Bad Request when podcast is not completed."""
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
    )
    res = await client.post(
        f"/api/podcasts/{podcast.id}/interrupt",
        headers=auth_header,
        json={"question": "Wait, I have a question", "current_time": 0.0},
    )
    assert res.status_code == 400
    assert "not yet completed" in res.json()["detail"]


@pytest.mark.asyncio
async def test_post_interrupt_endpoint_permissions(client, db, test_user, test_document, auth_header):
    """Verify another user cannot interrupt someone else podcast."""
    user_two = _create_user(db, "other_listener")
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="completed",
    )

    # User 2 tries to interrupt User 1 podcast
    res = await client.post(
        f"/api/podcasts/{podcast.id}/interrupt",
        headers=_header(user_two.id),
        json={"question": "Can I interrupt?", "current_time": 1.0},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_transcribe_endpoint(client, db, test_user, test_document, auth_header):
    """Verify POST /api/podcasts/{id}/transcribe uploads audio and returns transcription."""
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="completed",
    )

    with patch("app.services.podcast_interaction.transcribe_question", new_callable=AsyncMock) as mock_transcribe:
        mock_transcribe.return_value = "Can you explain this again?"

        files = {"file": ("question.wav", b"FAKEWAVDATA", "audio/wav")}
        res = await client.post(
            f"/api/podcasts/{podcast.id}/transcribe",
            headers=auth_header,
            files=files,
        )
        assert res.status_code == 200
        assert res.json() == {"transcript": "Can you explain this again?"}


@pytest.mark.asyncio
async def test_transcribe_endpoint_empty_file(client, db, test_user, test_document, auth_header):
    """Verify 400 Bad Request when uploading empty audio file."""
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="completed",
    )

    files = {"file": ("empty.wav", b"", "audio/wav")}
    res = await client.post(
        f"/api/podcasts/{podcast.id}/transcribe",
        headers=auth_header,
        files=files,
    )
    assert res.status_code == 400
    assert "Empty audio file" in res.json()["detail"]


@pytest.mark.asyncio
async def test_interrupt_audio_endpoint_missing_file(client, db, test_user, test_document, auth_header, tmp_path: Path):
    """Verify 404 when requested audio file does not exist on disk."""
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="completed",
    )

    with patch("app.config.settings.data_dir", str(tmp_path)):
        res = await client.get(
            f"/api/podcasts/{podcast.id}/interrupts/non_existent_id/audio",
            headers=auth_header,
        )
        assert res.status_code == 404
