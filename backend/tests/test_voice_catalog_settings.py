"""Tests for Kokoro voice catalog, voice settings normalization, and endpoint validation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, Summary, User, UserSettings
from app.services.llm_settings import get_default_voice, invalidate_cache
from app.voice_catalog import (
    DEFAULT_VOICE,
    KOKORO_VOICE_IDS,
    VOICE_CATALOG,
    is_valid_voice,
    normalize_voice,
)

_TEST_JWT_SECRET = "test_secret_32_chars_long_for_testing!"


def test_config_tts_url_renamed_and_piper_keys_removed():
    assert hasattr(settings, "tts_url")
    assert not hasattr(settings, "piper_tts_url")
    assert not hasattr(settings, "piper_voice_model")


def test_authoritative_voice_catalog_structure():
    assert len(VOICE_CATALOG) == 6
    catalog_ids = {voice["id"] for voice in VOICE_CATALOG}
    assert catalog_ids == set(KOKORO_VOICE_IDS)
    assert DEFAULT_VOICE in catalog_ids

    for voice in VOICE_CATALOG:
        assert voice["id"].startswith("kokoro-")
        assert "piper" not in voice["description"].lower()


def test_voice_helpers_normalize_and_validate():
    assert is_valid_voice("kokoro-af_heart")
    assert is_valid_voice("kokoro-af_bella")
    assert not is_valid_voice("en_US-lessac-medium")
    assert not is_valid_voice("default")
    assert not is_valid_voice(None)

    assert normalize_voice("kokoro-af_bella") == "kokoro-af_bella"
    assert normalize_voice("en_US-lessac-medium") == DEFAULT_VOICE
    assert normalize_voice("default") == DEFAULT_VOICE
    assert normalize_voice(None) == DEFAULT_VOICE
    assert normalize_voice("unknown_id") == DEFAULT_VOICE


@pytest.mark.asyncio
async def test_api_voices_catalog_returns_only_kokoro(client: AsyncClient):
    resp = await client.get("/api/tts/voices")
    assert resp.status_code == 200
    data = resp.json()
    assert "voices" in data
    returned_voices = data["voices"]
    assert len(returned_voices) == 6

    returned_ids = [voice["id"] for voice in returned_voices]
    assert returned_ids == list(KOKORO_VOICE_IDS)

    for voice in returned_voices:
        assert voice["id"].startswith("kokoro-")
        assert "piper" not in voice["label"].lower()
        assert "piper" not in voice["description"].lower()


@pytest.mark.asyncio
async def test_settings_voice_update_valid_kokoro(
    client: AsyncClient,
    auth_header: dict,
    test_user: User,
    db: Session,
):
    invalidate_cache(user_id=test_user.id)
    resp = await client.put(
        "/api/settings",
        json={"tts": {"default_voice": "kokoro-af_bella"}},
        headers=auth_header,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tts"]["default_voice"] == "kokoro-af_bella"

    get_resp = await client.get("/api/settings", headers=auth_header)
    assert get_resp.status_code == 200
    assert get_resp.json()["tts"]["default_voice"] == "kokoro-af_bella"
    assert get_default_voice(test_user.id, db=db) == "kokoro-af_bella"


@pytest.mark.asyncio
async def test_settings_voice_update_rejects_piper_and_unknown_voices(client: AsyncClient, auth_header: dict):
    invalid_voices = [
        "en_US-lessac-medium",
        "en_US-lessac-high",
        "en_US-ryan-medium",
        "en_US-ryan-high",
        "en_US-libritts_r-medium",
        "default",
        "host_a",
        "host_b",
        "unknown-voice",
    ]
    for voice in invalid_voices:
        resp = await client.put(
            "/api/settings",
            json={"tts": {"default_voice": voice}},
            headers=auth_header,
        )
        assert resp.status_code == 422, f"Expected 422 for voice: {voice}"


@pytest.mark.asyncio
async def test_legacy_piper_preference_normalized_and_persisted(
    client: AsyncClient,
    auth_header: dict,
    test_user: User,
    db: Session,
):
    invalidate_cache(user_id=test_user.id)
    row = db.query(UserSettings).filter(
        UserSettings.user_id == test_user.id,
        UserSettings.key == "prefs",
    ).first()
    if row:
        row.value = {"active": "omniroute", "tts": {"default_voice": "en_US-lessac-medium"}}
    else:
        row = UserSettings(
            user_id=test_user.id,
            key="prefs",
            value={"active": "omniroute", "tts": {"default_voice": "en_US-lessac-medium"}},
        )
        db.add(row)
    db.commit()

    resp = await client.get("/api/settings", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["tts"]["default_voice"] == DEFAULT_VOICE

    db.refresh(row)
    assert row.value["tts"]["default_voice"] == DEFAULT_VOICE
    assert get_default_voice(test_user.id, db=db) == DEFAULT_VOICE


@pytest.mark.asyncio
async def test_valid_kokoro_preferences_preserved_with_isolation(
    client: AsyncClient,
    auth_header: dict,
    test_user: User,
    second_user: User,
    db: Session,
):
    invalidate_cache(user_id=test_user.id)
    invalidate_cache(user_id=second_user.id)

    payload = {
        "sub": str(second_user.id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "type": "access",
    }
    second_token = jwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")
    second_auth_header = {"Authorization": f"Bearer {second_token}"}

    put1 = await client.put(
        "/api/settings",
        json={"tts": {"default_voice": "kokoro-af_bella"}},
        headers=auth_header,
    )
    assert put1.status_code == 200

    put2 = await client.put(
        "/api/settings",
        json={"tts": {"default_voice": "kokoro-am_michael"}},
        headers=second_auth_header,
    )
    assert put2.status_code == 200

    get1 = await client.get("/api/settings", headers=auth_header)
    assert get1.json()["tts"]["default_voice"] == "kokoro-af_bella"

    get2 = await client.get("/api/settings", headers=second_auth_header)
    assert get2.json()["tts"]["default_voice"] == "kokoro-am_michael"

    assert get_default_voice(test_user.id, db=db) == "kokoro-af_bella"
    assert get_default_voice(second_user.id, db=db) == "kokoro-am_michael"


@pytest.mark.asyncio
async def test_synthesis_endpoints_reject_removed_voices_with_422(
    client: AsyncClient,
    auth_header: dict,
    test_document: Document,
    db: Session,
    test_user: User,
):
    stream_resp = await client.post(
        "/api/tts/stream",
        json={"text": "Stream test", "voice": "en_US-lessac-medium"},
        headers=auth_header,
    )
    assert stream_resp.status_code == 422

    play_text_resp = await client.post(
        "/api/tts/play/text",
        json={"text": "Play text test", "voice": "en_US-lessac-medium"},
        headers=auth_header,
    )
    assert play_text_resp.status_code == 422

    summary = Summary(
        document_id=test_document.id,
        user_id=test_user.id,
        content="Test summary text for TTS.",
        mode="concise",
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)

    play_summary_resp = await client.post(
        f"/api/tts/play/{summary.id}?voice=en_US-lessac-medium",
        headers=auth_header,
    )
    assert play_summary_resp.status_code == 422

    play_page_resp = await client.post(
        f"/api/tts/play/page/{test_document.id}/1?voice=en_US-lessac-medium",
        headers=auth_header,
    )
    assert play_page_resp.status_code == 422


@pytest.mark.asyncio
async def test_synthesis_endpoints_accept_valid_kokoro_voice_or_default(
    client: AsyncClient,
    auth_header: dict,
    test_document: Document,
    db: Session,
    test_user: User,
):
    with patch("app.routers.tts.generate_tts_task.delay") as mock_task:
        mock_task.return_value = MagicMock(id="task-123")

        resp1 = await client.post(
            "/api/tts/play/text",
            json={"text": "Play text test", "voice": "kokoro-af_bella"},
            headers=auth_header,
        )
        assert resp1.status_code == 200
        mock_task.assert_called_once()
        assert mock_task.call_args[1]["voice"] == "kokoro-af_bella"

    expected_default = get_default_voice(test_user.id, db=db)
    with patch("app.routers.tts.generate_tts_task.delay") as mock_task:
        mock_task.return_value = MagicMock(id="task-124")

        resp2 = await client.post(
            "/api/tts/play/text",
            json={"text": "Play text test"},
            headers=auth_header,
        )
        assert resp2.status_code == 200
        mock_task.assert_called_once()
        assert mock_task.call_args[1]["voice"] == expected_default
