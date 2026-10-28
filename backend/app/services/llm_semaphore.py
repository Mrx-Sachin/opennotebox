"""Shared, bounded admission control for LLM requests.

Redis sorted-set leases make the limit apply across API and Celery processes.
Lease expiry recovers slots when a process exits without cleanup; a heartbeat
keeps long model calls from losing their slots while they are still running.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings

_ACQUIRE_SCRIPT = """
-- opennotebox:llm-admission:acquire
local clock = redis.call('TIME')
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then
  return 0
end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then
  return 0
end
local expires = now + tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], expires, ARGV[1])
redis.call('ZADD', KEYS[2], expires, ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[2]) * 2)
return 1
"""
_RENEW_SCRIPT = """
-- opennotebox:llm-admission:renew
local clock = redis.call('TIME')
local expires = (tonumber(clock[1]) * 1000)
  + math.floor(tonumber(clock[2]) / 1000)
  + tonumber(ARGV[2])
if not redis.call('ZSCORE', KEYS[1], ARGV[1])
  or not redis.call('ZSCORE', KEYS[2], ARGV[1]) then
  return 0
end
redis.call('ZADD', KEYS[1], expires, ARGV[1])
redis.call('ZADD', KEYS[2], expires, ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[2]) * 2)
return 1
"""
_RELEASE_SCRIPT = """
-- opennotebox:llm-admission:release
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""

logger = logging.getLogger(__name__)


class LLMAdmissionUnavailable(RuntimeError):
    """Raised when shared admission cannot be checked safely."""


class LLMAdmissionTimeout(TimeoutError):
    """Raised when an LLM request cannot get a slot before its wait limit."""


def _new_redis_client() -> Redis:
    """Create a short-lived Redis client for one admission lease."""
    return Redis.from_url(
        settings.llm_admission_redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        max_connections=1,
    )


def _lane_limit(lane: str) -> int:
    if lane == "foreground":
        return min(settings.llm_max_concurrency, settings.llm_foreground_max_concurrency)
    return min(settings.llm_max_concurrency, settings.llm_background_max_concurrency)


def _keys(lane: str) -> list[str]:
    return [
        f"{settings.llm_admission_key_prefix}:all",
        f"{settings.llm_admission_key_prefix}:{lane}",
    ]


def _wait_seconds(lane: str) -> float:
    if lane == "foreground":
        return settings.llm_foreground_wait_seconds
    return settings.llm_background_wait_seconds


async def _try_acquire(client: Redis, lane: str, token: str) -> bool:
    keys = _keys(lane)
    try:
        result = await client.eval(
            _ACQUIRE_SCRIPT,
            len(keys),
            *keys,
            token,
            settings.llm_admission_lease_seconds * 1000,
            settings.llm_max_concurrency,
            _lane_limit(lane),
        )
    except RedisError as exc:
        raise LLMAdmissionUnavailable("Redis LLM admission is unavailable") from exc
    return bool(result)


async def _renew_lease(client: Redis, lane: str, token: str, owner: asyncio.Task) -> None:
    keys = _keys(lane)
    interval = max(1.0, settings.llm_admission_lease_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        try:
            renewed = await client.eval(
                _RENEW_SCRIPT,
                len(keys),
                *keys,
                token,
                settings.llm_admission_lease_seconds * 1000,
            )
        except RedisError:
            logger.exception("Redis lease renewal failed; cancelling its LLM request")
            owner.cancel()
            return
        if not renewed:
            logger.error("Redis LLM admission lease expired while its request was active")
            owner.cancel()
            return


async def _release_lease(client: Redis, lane: str, token: str) -> None:
    keys = _keys(lane)
    try:
        await client.eval(_RELEASE_SCRIPT, len(keys), *keys, token)
    except RedisError:
        # The TTL is the crash-safe cleanup path. Do not replace an exception
        # from the model call with a secondary release failure.
        logger.exception("Could not release Redis LLM admission lease; it will expire")


@asynccontextmanager
async def _llm_admission(lane: str) -> AsyncIterator[None]:
    client = _new_redis_client()
    token = uuid.uuid4().hex
    owner = asyncio.current_task()
    if owner is None:  # pragma: no cover - async context managers require a task
        await client.aclose()
        raise RuntimeError("LLM admission requires an asyncio task")

    acquired = False
    heartbeat: asyncio.Task | None = None
    try:
        wait_seconds = _wait_seconds(lane)
        deadline = asyncio.get_running_loop().time() + wait_seconds
        delay = 0.1
        while True:
            acquired = await _try_acquire(client, lane, token)
            if acquired:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise LLMAdmissionTimeout(
                    f"No {lane} LLM slot became available within "
                    f"{wait_seconds:g} seconds"
                )
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.5)

        heartbeat = asyncio.create_task(_renew_lease(client, lane, token, owner))
        yield
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        if acquired:
            await _release_lease(client, lane, token)
        await client.aclose()


@asynccontextmanager
async def llm_semaphore() -> AsyncIterator[None]:
    """Acquire a foreground LLM slot for an interactive chat request."""
    async with _llm_admission("foreground"):
        yield


@asynccontextmanager
async def background_llm_semaphore() -> AsyncIterator[None]:
    """Acquire a background LLM slot for summaries and generated artifacts."""
    async with _llm_admission("background"):
        yield
