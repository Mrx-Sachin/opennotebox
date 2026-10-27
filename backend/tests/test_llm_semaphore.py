"""Tests for shared LLM admission and its foreground/background limits."""
from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from redis import Redis
from redis.exceptions import RedisError

from app.config import settings
from app.services import llm_semaphore as admission


async def _hold_slot(context, entered: asyncio.Event, release: asyncio.Event) -> None:
    async with context():
        entered.set()
        await release.wait()


@pytest.mark.asyncio
async def test_lanes_share_the_total_limit_and_cancel_releases_a_slot(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_foreground_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_background_max_concurrency", 1)
    monkeypatch.setattr(settings, "llm_foreground_wait_seconds", 0.05)
    monkeypatch.setattr(settings, "llm_background_wait_seconds", 0.05)

    foreground_entered = asyncio.Event()
    background_entered = asyncio.Event()
    foreground_release = asyncio.Event()
    background_release = asyncio.Event()
    foreground_task = asyncio.create_task(
        _hold_slot(admission.llm_semaphore, foreground_entered, foreground_release)
    )
    background_task = asyncio.create_task(
        _hold_slot(admission.background_llm_semaphore, background_entered, background_release)
    )

    try:
        await asyncio.wait_for(foreground_entered.wait(), timeout=1)
        await asyncio.wait_for(background_entered.wait(), timeout=1)

        with pytest.raises(admission.LLMAdmissionTimeout):
            async with admission.llm_semaphore():
                pytest.fail("the shared limit admitted a third request")

        foreground_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await foreground_task

        async with admission.llm_semaphore():
            pass
    finally:
        foreground_release.set()
        background_release.set()
        for task in (foreground_task, background_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(foreground_task, background_task, return_exceptions=True)

    async with admission.background_llm_semaphore():
        with pytest.raises(admission.LLMAdmissionTimeout):
            async with admission.background_llm_semaphore():
                pytest.fail("background lane exceeded its configured limit")


@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_redis_leases_are_shared_renewed_and_expire(monkeypatch) -> None:
    """Exercise the actual admission Lua scripts in an isolated Redis namespace."""
    redis_url = "redis://127.0.0.1:6379/15"
    sync_client = Redis.from_url(redis_url, socket_connect_timeout=1)
    try:
        sync_client.ping()
    except RedisError:
        sync_client.close()
        pytest.skip("local Redis is unavailable")

    prefix = f"deepread:test-llm-admission:{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "llm_admission_redis_url", redis_url)
    monkeypatch.setattr(settings, "llm_admission_key_prefix", prefix)
    monkeypatch.setattr(settings, "llm_max_concurrency", 24)
    monkeypatch.setattr(settings, "llm_foreground_max_concurrency", 20)
    monkeypatch.setattr(settings, "llm_background_max_concurrency", 4)
    monkeypatch.setattr(settings, "llm_foreground_wait_seconds", 0.15)
    monkeypatch.setattr(settings, "llm_background_wait_seconds", 0.15)
    monkeypatch.setattr(settings, "llm_admission_lease_seconds", 3)

    release = asyncio.Event()
    foreground_count = settings.llm_foreground_max_concurrency
    background_count = settings.llm_background_max_concurrency
    contexts = [admission.llm_semaphore] * foreground_count + [
        admission.background_llm_semaphore
    ] * background_count
    entered = [asyncio.Event() for _ in contexts]
    holders = [
        asyncio.create_task(_hold_slot(context, event, release))
        for context, event in zip(contexts, entered)
    ]
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in entered)), timeout=5
        )
        await asyncio.sleep(3.2)

        with pytest.raises(admission.LLMAdmissionTimeout):
            async with admission.llm_semaphore():
                pytest.fail("the configured foreground lane admitted a 21st chat")

        for task in (holders[0], holders[-1]):
            task.cancel()
        await asyncio.gather(holders[0], holders[-1], return_exceptions=True)
        async with admission.llm_semaphore():
            pass
        async with admission.background_llm_semaphore():
            pass

        release.set()
        await asyncio.gather(*holders, return_exceptions=True)

        monkeypatch.setattr(settings, "llm_admission_lease_seconds", 1)
        client = admission._new_redis_client()
        try:
            assert await admission._try_acquire(client, "foreground", "crashed")
        finally:
            await client.aclose()
        await asyncio.sleep(1.1)
        client = admission._new_redis_client()
        try:
            assert await admission._try_acquire(client, "foreground", "after-expiry")
        finally:
            await client.aclose()
    finally:
        release.set()
        for task in holders:
            if not task.done():
                task.cancel()
        await asyncio.gather(*holders, return_exceptions=True)
        for key in (f"{prefix}:all", f"{prefix}:foreground", f"{prefix}:background"):
            sync_client.delete(key)
        sync_client.close()
