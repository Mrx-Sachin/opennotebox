"""LLM concurrency semaphore.

Limits the number of simultaneous LLM requests to protect single-GPU backends
from OOM. Default of 2 concurrent calls fits comfortably on consumer GPUs
(24-48 GiB VRAM) without throttling through a single serial queue.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from app.config import settings

_semaphore = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy-init the semaphore from the current settings value."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
    return _semaphore


@asynccontextmanager
async def llm_semaphore():
    """Async context manager that acquires the LLM concurrency semaphore.

    Use to wrap any LLM call that shares a single-GPU backend:
        async with llm_semaphore():
            response = await model.ainvoke(messages)
    """
    sem = _get_semaphore()
    await sem.acquire()
    try:
        yield
    finally:
        sem.release()
