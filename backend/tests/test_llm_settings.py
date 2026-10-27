"""Tests for the LLM settings API: per-user active backend, the shared
backend registry, and GET /api/settings/llm/health."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session


BACKENDS_ROW = [
    {
        "name": "alpha",
        "base_url": "http://alpha:8000/v1",
        "model": "model-a",
        "api_key": "key-a",
    },
    {
        "name": "beta",
        "base_url": "http://beta:8000/v1",
        "model": "model-b",
        "api_key": None,
    },
    {
        "name": "empty-url",
        "base_url": "",
        "model": "model-c",
        "api_key": None,
    },
]

HEALTHY_PROBE = {
    "status": "healthy",
    "latency_ms": 12.0,
    "base_url": "http://alpha:8000/v1",
    "available_models": ["model-a", "other"],
}

UNHEALTHY_PROBE = {
    "status": "unhealthy",
    "error": "connection refused",
    "latency_ms": 1.0,
    "base_url": "http://beta:8000/v1",
}


@pytest.fixture()
def llm_registry(db: Session):
    """Seed the deployment-wide backend registry with three backends and
    disable the config.py seed backends for the whole test, so tests don't
    depend on which internal hosts (vllm, ninja) exist on the dev box.
    Both the router's backfill and the fresh-user active default read the
    seeds; with them empty, the registry above is the whole world."""
    from app.models import AppSettings

    row = AppSettings(key="llm_backends", value={"backends": [dict(b) for b in BACKENDS_ROW]})
    db.add(row)
    db.commit()
    with (
        patch("app.routers.settings._seed_backends", return_value=[]),
        patch("app.services.llm_settings._seed_backends", return_value=[]),
    ):
        yield row


def _fake_check(base_url: str, model: str, api_key: str = "ollama"):
    """check_llm_health stand-in: the alpha endpoint (model "model-a") is
    healthy, everything else dead. Signature mirrors the real function."""
    return (
        dict(HEALTHY_PROBE)
        if model == "model-a"
        else dict(UNHEALTHY_PROBE, base_url=base_url)
    )


async def test_get_settings_seeds_prefs_and_returns_backends(
    client: AsyncClient, db: Session, auth_header: dict, llm_registry
):
    res = await client.get("/api/settings", headers=auth_header)
    assert res.status_code == 200
    llm = res.json()["llm"]
    names = [b["name"] for b in llm["backends"]]
    # The router backfills the config.py seed backends into any registry,
    # so assert our three are present rather than an exact list.
    assert {"alpha", "beta", "empty-url"} <= set(names)
    # A fresh user's active backend falls back to the first configured one.
    assert llm["active"] == "alpha"
    # Raw api_key must never be echoed back.
    assert all("api_key" not in b or b.get("api_key") is None for b in llm["backends"])


async def test_put_active_backend_round_trips(
    client: AsyncClient, db: Session, auth_header: dict, llm_registry
):
    res = await client.put(
        "/api/settings",
        headers=auth_header,
        json={"llm": {"active": "beta"}},
    )
    assert res.status_code == 200
    assert res.json()["llm"]["active"] == "beta"

    res = await client.get("/api/settings", headers=auth_header)
    assert res.json()["llm"]["active"] == "beta"


async def test_active_falls_back_when_backend_removed(
    client: AsyncClient, db: Session, auth_header: dict, admin_auth_header: dict, llm_registry
):
    # Make "beta" active, then delete it from the shared registry.
    res = await client.put("/api/settings", headers=auth_header, json={"llm": {"active": "beta"}})
    assert res.status_code == 200

    res = await client.put(
        "/api/settings",
        headers=admin_auth_header,
        json={"llm": {"backends": [dict(b) for b in BACKENDS_ROW if b["name"] != "beta"]}},
    )
    assert res.status_code == 200
    # The stored active name no longer exists - it must not point at
    # nothing.
    assert res.json()["llm"]["active"] in {"alpha", "empty-url"}


async def test_health_endpoint_reports_per_backend_status(
    client: AsyncClient, db: Session, auth_header: dict, llm_registry
):
    # First make beta the active backend so the response's `active` is
    # not trivially the registry default.
    await client.put("/api/settings", headers=auth_header, json={"llm": {"active": "beta"}})

    with patch("app.routers.settings.check_llm_health", side_effect=_fake_check):
        res = await client.get("/api/settings/llm/health", headers=auth_header)
    assert res.status_code == 200
    body = res.json()
    assert body["active"] == "beta"
    by_name = {b["name"]: b for b in body["backends"]}
    assert set(by_name) == {"alpha", "beta", "empty-url"}
    assert by_name["alpha"]["status"] == "healthy"
    assert by_name["alpha"]["available_models"] == ["model-a", "other"]
    assert by_name["beta"]["status"] == "unhealthy"
    assert "connection refused" in by_name["beta"]["error"]
    # A backend with no configured endpoint is reported, never probed.
    assert by_name["empty-url"]["status"] == "unhealthy"
    assert "No endpoint" in by_name["empty-url"]["error"]
