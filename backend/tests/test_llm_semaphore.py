"""Tests for shared LLM admission and its foreground/background limits."""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from redis import Redis
from redis.exceptions import RedisError

from app.config import settings
from app.services import llm_semaphore as admission

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

@pytest.fixture(scope="module")
def disposable_redis_url():
    port = _find_free_port()
    container_name = f"opennotebox-test-redis-{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                container_name,
                "--memory=64m",
                "--memory-swap=64m",
                "--cpus=0.5",
                "-p",
                f"127.0.0.1:{port}:6379",
                "redis:7-alpine",
            ],
            check=True,
            capture_output=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        pytest.skip(f"Disposable Redis requires Docker: {exc}")

    redis_url = f"redis://127.0.0.1:{port}/0"
    try:
        for _ in range(50):
            try:
                with Redis.from_url(redis_url, socket_connect_timeout=0.2) as client:
                    if client.ping():
                        break
            except RedisError:
                time.sleep(0.05)
        else:
            pytest.fail("Disposable Redis did not become ready")
        yield redis_url
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

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
async def test_redis_leases_are_shared_renewed_and_expire(
    monkeypatch, disposable_redis_url
) -> None:
    sync_client = Redis.from_url(disposable_redis_url, socket_connect_timeout=1)
    prefix = f"opennotebox:test-llm-admission:{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "llm_admission_redis_url", disposable_redis_url)
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

@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_connection_reuse_within_single_loop(monkeypatch, disposable_redis_url) -> None:
    sync_client = Redis.from_url(disposable_redis_url, socket_connect_timeout=1)
    prefix = f"opennotebox:test-reuse:{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "llm_admission_redis_url", disposable_redis_url)
    monkeypatch.setattr(settings, "llm_admission_key_prefix", prefix)
    monkeypatch.setattr(settings, "llm_max_concurrency", 10)
    monkeypatch.setattr(settings, "llm_foreground_max_concurrency", 10)
    monkeypatch.setattr(settings, "llm_foreground_wait_seconds", 1.0)
    monkeypatch.setattr(settings, "llm_admission_lease_seconds", 10)

    stats_before = sync_client.info("stats")
    conn_start = stats_before["total_connections_received"]

    for _ in range(8):
        async with admission.llm_semaphore():
            pass

    async def _concurrent_lease():
        async with admission.llm_semaphore():
            await asyncio.sleep(0.05)

    await asyncio.gather(*[_concurrent_lease() for _ in range(5)])

    stats_after = sync_client.info("stats")
    conn_opened = stats_after["total_connections_received"] - conn_start

    assert conn_opened <= 5
    sync_client.close()

@pytest.mark.redis_integration
def test_repeated_asyncio_run_loops_deterministic_closure(monkeypatch, disposable_redis_url) -> None:
    prefix = f"opennotebox:test-loops:{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "llm_admission_redis_url", disposable_redis_url)
    monkeypatch.setattr(settings, "llm_admission_key_prefix", prefix)
    monkeypatch.setattr(settings, "llm_max_concurrency", 10)
    monkeypatch.setattr(settings, "llm_foreground_max_concurrency", 10)
    monkeypatch.setattr(settings, "llm_foreground_wait_seconds", 1.0)
    monkeypatch.setattr(settings, "llm_admission_lease_seconds", 10)

    async def _run_in_loop(loop_index: int):
        for _ in range(3):
            async with admission.llm_semaphore():
                pass

    for i in range(5):
        asyncio.run(_run_in_loop(i))

    current_pid = os.getpid()
    active_keys = [k for k in admission._client_cache.keys() if k[0] == current_pid]
    assert len(active_keys) == 0

@pytest.mark.redis_integration
def test_celery_fork_isolation(monkeypatch, disposable_redis_url) -> None:
    script = """
import asyncio
import os
import sys
from app.config import settings
from app.services import llm_semaphore as admission

settings.llm_admission_redis_url = sys.argv[1]
settings.llm_admission_key_prefix = sys.argv[2]

async def lease():
    async with admission.llm_semaphore():
        pass

parent_loop = asyncio.new_event_loop()
parent_loop.run_until_complete(lease())
assert admission._client_cache
pid = os.fork()
if pid == 0:
    try:
        asyncio.run(lease())
        assert not admission._client_cache
        os._exit(0)
    except BaseException:
        os._exit(1)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
parent_loop.run_until_complete(lease())
parent_loop.run_until_complete(admission.close_llm_semaphore())
parent_loop.close()
assert not admission._client_cache
"""
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", script, disposable_redis_url,
         f"fork:{uuid.uuid4().hex}"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr

@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_fail_closed_on_redis_error(monkeypatch) -> None:
    dead_port = _find_free_port()
    monkeypatch.setattr(settings, "llm_admission_redis_url", f"redis://127.0.0.1:{dead_port}/0")
    monkeypatch.setattr(settings, "llm_admission_key_prefix", "opennotebox:test-dead")

    with pytest.raises(admission.LLMAdmissionUnavailable):
        async with admission.llm_semaphore():
            pytest.fail("Should have raised LLMAdmissionUnavailable")

@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_explicit_closure_via_close_llm_semaphore(monkeypatch, disposable_redis_url) -> None:
    prefix = f"opennotebox:test-close:{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "llm_admission_redis_url", disposable_redis_url)
    monkeypatch.setattr(settings, "llm_admission_key_prefix", prefix)

    async with admission.llm_semaphore():
        pass

    loop = asyncio.get_running_loop()
    key = (os.getpid(), id(loop))
    assert key in admission._client_cache

    await admission.close_llm_semaphore()
    assert key not in admission._client_cache


@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_burst_queues_with_bounded_connections(monkeypatch, disposable_redis_url):
    monkeypatch.setattr(settings, "llm_admission_redis_url", disposable_redis_url)
    monkeypatch.setattr(settings, "llm_admission_key_prefix", f"burst:{uuid.uuid4().hex}")
    monkeypatch.setattr(settings, "llm_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_foreground_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_foreground_wait_seconds", 10)
    active = 0
    peak = 0
    completed = 0

    async def request():
        nonlocal active, peak, completed
        async with admission.llm_semaphore():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            active -= 1
            completed += 1

    try:
        await asyncio.gather(*(request() for _ in range(30)))
        assert completed == 30
        assert peak <= 2
        assert admission._get_redis_client().connection_pool.max_connections == 2
    finally:
        await admission.close_llm_semaphore()


@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_old_pool_cleanup_preserves_replacement(monkeypatch, disposable_redis_url):
    monkeypatch.setattr(settings, "llm_admission_redis_url", disposable_redis_url)
    old_client = admission._get_redis_client()
    await old_client.ping()
    monkeypatch.setattr(settings, "llm_admission_redis_url", disposable_redis_url[:-1] + "1")
    replacement = admission._get_redis_client()
    await replacement.ping()
    assert replacement is not old_client
    assert admission._get_redis_client() is replacement
    await admission.close_llm_semaphore()
