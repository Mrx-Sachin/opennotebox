"""Reads the active LLM backend / TTS defaults, with short caches so hot
call sites (e.g. per-page summarization loops) don't hit the DB on every
call.

Two data sources, both described in models/__init__.py and written by
routers/settings.py and routers/flashcards.py:
  - AppSettings (key="llm_backends"): the deployment-wide registry of
    configured LLM endpoints. Shared by every user.
  - UserSettings (key="prefs"): one user's choice of *which* backend is
    active, which model they run on it (model_overrides), and their TTS
    narration voice. NOT shared.

Used by summarizer.py, chat_engine.py, tasks/podcast_tasks.py,
tasks/flashcard_tasks.py, tasks/summary_tasks.py, tasks/tts_tasks.py,
routers/tts.py.

get_active_llm()/get_default_voice() accept an optional user_id; every real
call site now threads through the document/conversation owner's id, all the
way from the Celery task or router down through summarizer.py/chat_engine.py/
podcast_tasks.py/flashcard_tasks.py's model-building helpers. This used to
not be true: several call sites called these with no arguments, which does
NOT fall back to "the deployment default" - it falls back to
_seed_user_prefs()'s hardcoded default, silently ignoring whatever backend a
user actually selected in Settings. That was live in production: a user
configured and selected a vLLM backend, and every chat/summary/podcast/
flashcard request kept dialing Ollama instead, with no error and no log line
pointing at the cause. user_id=None remains a legitimate value for contexts
with no authenticated user in scope (a background sweep with no owner), and
degrades to the deployment default rather than raising - it should never be
the default *because a caller forgot to pass one*.
"""
import time
from typing import Optional
from sqlalchemy.orm import Session

from app.voice_catalog import DEFAULT_VOICE, is_valid_voice, normalize_voice

from loguru import logger

from app.config import settings

_CACHE_TTL_SECONDS = 15

# Deployment-wide LLM backend registry cache. Exactly one deployment shares
# this, so a single unkeyed slot is safe - no per-user isolation needed.
_backends_cache: dict = {"value": None, "expires": 0.0}

# Per-user preference cache (active backend name + TTS voice), keyed by
# user_id. This is the piece that has to be keyed correctly: with a single
# shared slot (the old design), user A reading settings right after user B's
# PUT could serve user B's freshly-cached values to user A, and user A's own
# PUT would invalidate the one slot out from under user B mid-read. Keying
# per user_id makes each user's cache entry independent, so concurrent
# reads/writes from different users can never cross-contaminate.
_user_cache: dict[int, dict] = {}


def _seed_backends() -> list[dict]:
    """Default LLM backend registry, seeded from config.py on first read.

    Single default: omniroute running antigravity/gemini-3.8-flash-tiered via local route.
    Extra backends can still be added from Settings; they are just not
    pre-seeded anymore.
    """
    backends = []
    if settings.effective_meta_api_key:
        backends.append({
            "name": "meta",
            "base_url": "https://api.meta.ai/v1",
            "model": settings.meta_llm_model,
            "api_key": settings.effective_meta_api_key,
        })
    backends.append({
        "name": "omniroute",
        "base_url": settings.vllm_base_url,
        "model": settings.vllm_model,
        "api_key": settings.vllm_api_key or "not-needed",
    })
    return backends


def _default_active_backend() -> str:
    """Name of the backend a fresh user should start on.

    Deliberately the first backend that is actually CONFIGURED (non-empty
    base_url) rather than a hardcoded name: vLLM is optional and ships with
    an empty URL, so hardcoding "vllm-h100" as active handed every new user a
    backend that cannot answer, and the failure surfaces much later as an
    unexplained chat error rather than as an obviously unconfigured setting.
    """
    for backend in _seed_backends():
        if backend.get("base_url"):
            return str(backend["name"])
    return "vllm-h100"


def _seed_default() -> dict:
    """Full default settings shape (backends + active + tts). Used by
    routers/settings.py to seed a brand-new deployment's backend registry
    and a brand-new user's preferences in one call, matching the shape the
    settings API has always exposed to clients."""
    return {
        "llm": {"backends": _seed_backends(), "active": _default_active_backend()},
        "tts": {"default_voice": DEFAULT_VOICE},
    }


def _seed_user_prefs() -> dict:
    """Default shape for a user's UserSettings(key="prefs") row.

    model_overrides maps a backend name to the model THIS user wants to run
    on it, layered over the deployment-wide registry entry. Empty by default:
    a fresh user just gets whatever each backend is configured with.
    """
    return {
        "active": _default_active_backend(),
        "model_overrides": {},
        "tts": {"default_voice": DEFAULT_VOICE},
    }


def _load_backends() -> list[dict]:
    """Load the deployment-wide LLM backend registry, cached for ~15s."""
    now = time.time()
    if _backends_cache["value"] is not None and now < _backends_cache["expires"]:
        return _backends_cache["value"]

    data = _seed_backends()
    try:
        from app.database import SessionLocal
        from app.models import AppSettings

        db = SessionLocal()
        try:
            row = db.query(AppSettings).filter(AppSettings.key == "llm_backends").first()
            if row and row.value and row.value.get("backends"):
                data = row.value["backends"]
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"llm_settings: failed to load llm_backends, using defaults: {e}")

    _backends_cache["value"] = data
    _backends_cache["expires"] = now + _CACHE_TTL_SECONDS
    return data


def _load_user_prefs(user_id: Optional[int], db: Optional[Session] = None) -> dict:
    """Load one user's preferences (active backend + TTS voice), cached per
    user_id for ~15s. user_id=None (an un-migrated call site - see module
    docstring) returns the deployment default rather than guessing at any
    particular user's settings."""
    if user_id is None:
        return _seed_user_prefs()

    now = time.time()
    cached = _user_cache.get(user_id)
    if cached is not None and now < cached["expires"]:
        return cached["value"]

    data = _seed_user_prefs()
    try:
        from app.database import SessionLocal
        from app.models import UserSettings

        session = db if db is not None else SessionLocal()
        try:
            row = (
                session.query(UserSettings)
                .filter(UserSettings.user_id == user_id, UserSettings.key == "prefs")
                .first()
            )
            if row and row.value:
                data = dict(row.value)
                tts_prefs = dict(data.get("tts", {}) or {})
                current_voice = tts_prefs.get("default_voice")
                if not is_valid_voice(current_voice):
                    tts_prefs["default_voice"] = DEFAULT_VOICE
                    data["tts"] = tts_prefs
                    row.value = data
                    session.commit()
        finally:
            if db is None:
                session.close()
    except Exception as e:
        logger.warning(f"llm_settings: failed to load prefs for user {user_id}, using defaults: {e}")

    _user_cache[user_id] = {"value": data, "expires": now + _CACHE_TTL_SECONDS}
    return data


def invalidate_cache(user_id: Optional[int] = None) -> None:
    """Force the next read to hit the DB.

    invalidate_cache() (no args): clears the deployment-wide backend
    registry cache. Call after the backend list itself changes (added/
    removed/edited an endpoint) - that's shared infra, every user's next
    read should see it.

    invalidate_cache(user_id=N): clears only that one user's preference
    cache slot. Call after a user changes their active backend or TTS
    voice. Deliberately does NOT touch other users' cached entries or the
    backend registry cache - a concurrent PUT from user B must never
    invalidate (or worse, appear to invalidate) user A's still-valid cache.
    """
    if user_id is None:
        _backends_cache["value"] = None
        _backends_cache["expires"] = 0.0
    else:
        _user_cache.pop(user_id, None)


def get_active_llm(user_id: Optional[int] = None) -> dict:
    """Return {"name", "base_url", "model", "api_key"} for user_id's
    currently active LLM backend. `name` is the registry name of the
    backend ("config-default" on the fallback path), so error messages can
    tell the user WHICH backend failed instead of a generic "AI backend is
    unreachable". Falls back to config.py defaults if settings are missing
    or malformed. See module docstring re: user_id=None callers."""
    fallback = {
        "name": "config-default",
        "base_url": settings.ollama_base_url,
        "model": settings.ollama_model_summary,
        "api_key": "ollama",
    }
    try:
        backends = _load_backends()
        prefs = _load_user_prefs(user_id)
        active_name = prefs.get("active")
        active = next((b for b in backends if b.get("name") == active_name), None)
        if active is None and backends:
            active = backends[0]
        if not active:
            return fallback
        # A per-user model override wins over the registry's model. The
        # registry is deployment-wide - without this, one user picking a
        # model in the chat picker silently moved everybody else on the
        # deployment onto it (see HANDOFF_LONGTERM.md 4a).
        overrides = prefs.get("model_overrides") or {}
        model = overrides.get(active.get("name")) or active.get("model")
        return {
            "name": active.get("name") or "config-default",
            "base_url": active.get("base_url") or fallback["base_url"],
            "model": model or fallback["model"],
            "api_key": active.get("api_key") or "ollama",
        }
    except Exception as e:
        logger.warning(f"get_active_llm: falling back to config.py defaults: {e}")
        return fallback


def get_default_voice(user_id: Optional[int] = None, db: Optional[Session] = None) -> str:
    """Return user_id's configured default TTS voice name."""
    try:
        prefs = _load_user_prefs(user_id, db=db)
        raw_voice = (prefs.get("tts", {}) or {}).get("default_voice")
        return normalize_voice(raw_voice)
    except Exception:
        return DEFAULT_VOICE
