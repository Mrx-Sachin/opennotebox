"""Settings API: LLM backend switching + TTS defaults.

Two-tier storage (see models/__init__.py for the full rationale):
  - AppSettings (key="llm_backends"): the deployment-wide registry of
    configured LLM endpoints (name/base_url/model/api_key). Shared by every
    user of this deployment - an admin adds/edits/removes endpoints and
    everyone sees the same list. Writing the registry is admin-only: a
    non-admin who repoints a shared backend's base_url silently intercepts
    every other user's document content and prompts.
  - UserSettings (key="prefs"), one row per user: which of those backends
    is *active* for them, which model they run on each (model_overrides),
    and their TTS narration voice. These are genuine
    personal preferences - two people using the app at once must be able to
    pick different narration voices or different active backends without
    stepping on each other.

GET/PUT /api/settings keeps the exact same request/response shape as
before this split (SettingsResponse/SettingsUpdate in schemas.py, which
this file does not own and does not change) - it just assembles that shape
from two sources instead of one now.

Consumed at call time by services/llm_settings.py (summarizer.py,
tasks/podcast_tasks.py, tasks/flashcard_tasks.py) and by routers/tts.py for
the default voice. NOT wired into services/chat_engine.py - that module is
off-limits here (owned by another engineer); see HANDOFF.md TODO.
"""
import asyncio

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, AppSettings, UserSettings
from app.schemas import (
    SettingsResponse,
    SettingsUpdate,
    LLMSettingsOut,
    LLMBackendOut,
    TTSSettings,
    ExportSettings,
    LLMBackendHealthOut,
    LLMHealthResponse,
    LLMProbeIn,
    LLMProbeOut,
)
from app.routers.auth import get_current_user_from_token
from app.services.export_service import EXPORT_KEY
from app.services.llm_monitor import check_llm_health
from app.services.llm_settings import _seed_backends, _seed_user_prefs, invalidate_cache

router = APIRouter(prefix="/api/settings", tags=["settings"])

BACKENDS_KEY = "llm_backends"
PREFS_KEY = "prefs"


def _ensure_default_backends(backends: list) -> tuple[list, bool]:
    """Merge missing seed backends and fill empty seed fields from config.

    Adds any seed backend that is not in the registry yet. For a seed
    backend that already exists but has an empty base_url or model (the
    usual state after a first boot with VLLM_* unset), copy those fields
    from the current seed so a later .env update takes effect without a
    manual Settings edit. Never overwrites a non-empty URL or model a
    user already configured. Returns (backends, changed).
    """
    by_name = {b.get("name"): b for b in backends}
    changed = False
    merged = list(backends)
    for seed in _seed_backends():
        name = seed["name"]
        if name not in by_name:
            merged.append(dict(seed))
            changed = True
            continue
        existing = by_name[name]
        for field in ("base_url", "model", "api_key"):
            if not (existing.get(field) or "").strip() and (seed.get(field) or "").strip():
                existing[field] = seed[field]
                changed = True
    return merged, changed


def _get_or_seed_backends(db: Session) -> AppSettings:
    row = db.query(AppSettings).filter(AppSettings.key == BACKENDS_KEY).first()
    if row is None:
        row = AppSettings(key=BACKENDS_KEY, value={"backends": _seed_backends()})
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    # Backfill newly-added default backends (e.g. vllm-h100) without
    # clobbering user customizations.
    backends, changed = _ensure_default_backends(list(row.value.get("backends", []) or []))
    if changed:
        row.value = {"backends": backends}
        db.commit()
        db.refresh(row)
        invalidate_cache()  # deployment-wide registry changed - clear the shared cache
    return row


def _get_or_seed_prefs(db: Session, user_id: int) -> UserSettings:
    row = (
        db.query(UserSettings)
        .filter(UserSettings.user_id == user_id, UserSettings.key == PREFS_KEY)
        .first()
    )
    if row is None:
        row = UserSettings(user_id=user_id, key=PREFS_KEY, value=_seed_user_prefs())
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _get_or_seed_export(db: Session, user_id: int) -> UserSettings:
    """The export-to-folder opt-in toggle. A distinct UserSettings row
    (key="export") rather than folded into "prefs" - it's not an LLM/TTS
    preference and export_tasks.py's hourly beat task needs to scan just
    this key across all users without also deserializing everyone's backend
    prefs blob.
    """
    row = (
        db.query(UserSettings)
        .filter(UserSettings.user_id == user_id, UserSettings.key == EXPORT_KEY)
        .first()
    )
    if row is None:
        row = UserSettings(user_id=user_id, key=EXPORT_KEY, value={"enabled": False})
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _to_response(backends: list, prefs: dict, export: dict) -> SettingsResponse:
    active = prefs.get("active") or (backends[0]["name"] if backends else "")
    overrides = prefs.get("model_overrides") or {}
    # If the user's stored active backend was since removed from the shared
    # registry (another user deleted it), fall back to the first remaining
    # one for display rather than pointing at nothing.
    backend_names = {b.get("name") for b in backends}
    if active not in backend_names and backends:
        active = backends[0]["name"]
    tts = prefs.get("tts", {}) or {}
    return SettingsResponse(
        llm=LLMSettingsOut(
            backends=[
                LLMBackendOut(
                    name=b.get("name", ""),
                    base_url=b.get("base_url", ""),
                    model=b.get("model", ""),
                    # What this user actually runs; `model` stays the shared
                    # registry value so a client PUTting backends back does
                    # not write one user's choice into everyone's config.
                    effective_model=(
                        overrides.get(b.get("name")) or b.get("model", "")
                    ),
                    api_key_set=bool(b.get("api_key")),
                )
                for b in backends
            ],
            active=active,
        ),
        tts=TTSSettings(default_voice=tts.get("default_voice") or "default"),
        export=ExportSettings(enabled=bool(export.get("enabled"))),
    )


@router.get("", response_model=SettingsResponse)
def get_settings(
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    backends_row = _get_or_seed_backends(db)
    prefs_row = _get_or_seed_prefs(db, user.id)
    export_row = _get_or_seed_export(db, user.id)
    return _to_response(backends_row.value.get("backends", []) or [], prefs_row.value, export_row.value)


@router.get("/llm/health", response_model=LLMHealthResponse)
async def get_llm_health(
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Probe every configured backend from the API container and report
    per-backend reachability, plus which one this user has active.

    The frontend can no longer tell a dead bridge from a live one (browsers
    can't reach host.docker.internal, and the stored settings row just says
    a backend exists - which is how a dead Grok bridge showed "Connected"
    until a chat request 503'd). Each probe is a 5s-capped /v1/models GET;
    they run concurrently so a full sweep is bounded by the slowest backend,
    not the sum.
    """
    backends_row = _get_or_seed_backends(db)
    prefs_row = _get_or_seed_prefs(db, user.id)
    backends = backends_row.value.get("backends", []) or []
    active = prefs_row.value.get("active") or (
        backends[0]["name"] if backends else ""
    )

    async def probe(b: dict) -> LLMBackendHealthOut:
        base_url = b.get("base_url", "")
        model = b.get("model", "")
        api_key = b.get("api_key") or "ollama"
        if not base_url:
            # Never configured - not a connection failure, just absent.
            return LLMBackendHealthOut(
                name=b.get("name", ""),
                status="unhealthy",
                error="No endpoint configured",
            )
        result = await asyncio.to_thread(check_llm_health, base_url, model, api_key)
        return LLMBackendHealthOut(
            name=b.get("name", ""),
            status=result.get("status", "unhealthy"),
            latency_ms=result.get("latency_ms"),
            available_models=result.get("available_models"),
            error=result.get("error"),
            http_status=result.get("http_status"),
        )

    results = await asyncio.gather(*[probe(b) for b in backends])
    return LLMHealthResponse(active=active, backends=list(results))


def _probe_base_url(raw: str) -> str:
    url = (raw or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="base_url must be an http(s) URL")
    if len(url) > 500:
        raise HTTPException(status_code=400, detail="base_url is too long")
    return url.rstrip("/")


@router.post("/llm/probe", response_model=LLMProbeOut)
async def probe_llm_endpoint(
    body: LLMProbeIn,
    user: User = Depends(get_current_user_from_token),
):
    """List models on an OpenAI-compatible endpoint before it is saved.

    The browser cannot call most providers' /v1/models (CORS); Grok/CLI
    bridges work only because they listen on loopback. This probe runs from
    the API container so adding a custom model is the same shape as
    connecting Grok: enter URL + key, pick from the list the endpoint
    actually serves.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only administrators can probe model endpoints")
    base_url = _probe_base_url(body.base_url)
    result = await asyncio.to_thread(
        check_llm_health, base_url, "", body.api_key or "ollama"
    )
    return LLMProbeOut(
        status=result.get("status", "unhealthy"),
        latency_ms=result.get("latency_ms"),
        available_models=result.get("available_models"),
        error=result.get("error"),
        http_status=result.get("http_status"),
    )


@router.put("", response_model=SettingsResponse)
def update_settings(
    body: SettingsUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    # The backends registry is deployment-wide: whoever writes it controls
    # where EVERY user's documents and prompts are sent. Admin-only. Other
    # parts of this PUT (own active backend, overrides, TTS, export) stay
    # per-user and open to everyone.
    if body.llm is not None and body.llm.backends is not None and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin privilege required to change the shared LLM backends",
        )

    backends_row = _get_or_seed_backends(db)
    prefs_row = _get_or_seed_prefs(db, user.id)
    export_row = _get_or_seed_export(db, user.id)
    backends = list(backends_row.value.get("backends", []) or [])
    prefs = dict(prefs_row.value)
    export = dict(export_row.value)

    backends_changed = False
    prefs_changed = False
    export_changed = False

    if body.llm is not None:
        if body.llm.backends is not None:
            existing_backends = {b["name"]: b for b in backends}
            new_backends = []
            for b in body.llm.backends:
                entry = {"name": b.name, "base_url": b.base_url, "model": b.model}
                # Preserve a previously-set api_key if the client didn't send
                # a new one for a backend with the same name (PUT is
                # partial - omitting api_key must not silently clear it).
                if b.api_key is not None:
                    entry["api_key"] = b.api_key
                elif b.name in existing_backends:
                    entry["api_key"] = existing_backends[b.name].get("api_key")
                else:
                    entry["api_key"] = None
                new_backends.append(entry)
            backends = new_backends
            backends_changed = True

        if body.llm.active is not None:
            prefs["active"] = body.llm.active
            prefs_changed = True

        if body.llm.model_overrides is not None:
            # Merge, don't replace: a client changing one backend's model
            # must not have to resend (or even know about) the others.
            # An explicit null drops the override back to the registry model.
            overrides = dict(prefs.get("model_overrides") or {})
            for name, model in body.llm.model_overrides.items():
                if model:
                    overrides[name] = model
                else:
                    overrides.pop(name, None)
            prefs["model_overrides"] = overrides
            prefs_changed = True

        # Keep this user's `active` valid: fall back to the first backend if
        # the active name no longer exists among the (possibly just-replaced)
        # backends. This only ever touches the requesting user's own prefs
        # row, never another user's.
        backend_names = {b["name"] for b in backends}
        if prefs.get("active") not in backend_names and backend_names:
            prefs["active"] = next(iter(backend_names))
            prefs_changed = True

        # Drop overrides for backends that no longer exist, so a deleted and
        # later re-added endpoint does not resurrect a stale model choice.
        stored_overrides = prefs.get("model_overrides") or {}
        pruned = {k: v for k, v in stored_overrides.items() if k in backend_names}
        if pruned != stored_overrides:
            prefs["model_overrides"] = pruned
            prefs_changed = True

    if body.tts is not None:
        tts = dict(prefs.get("tts", {}) or {})
        tts["default_voice"] = body.tts.default_voice
        prefs["tts"] = tts
        prefs_changed = True

    if body.export is not None:
        export["enabled"] = body.export.enabled
        export_changed = True

    if backends_changed:
        backends_row.value = {"backends": backends}
        db.commit()
        invalidate_cache()  # deployment-wide - visible to every user's next read

    if prefs_changed:
        prefs_row.value = prefs
        db.commit()
        invalidate_cache(user_id=user.id)  # this user only - never affects others' cached prefs

    if export_changed:
        export_row.value = export
        db.commit()
        # No llm_settings cache entry to invalidate for this key - it isn't
        # read through that cache (see _get_or_seed_export), only fetched
        # directly here and by the hourly export beat task.

    db.refresh(backends_row)
    db.refresh(prefs_row)
    db.refresh(export_row)
    return _to_response(backends_row.value.get("backends", []) or [], prefs_row.value, export_row.value)
