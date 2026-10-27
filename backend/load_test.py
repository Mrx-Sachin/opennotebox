#!/usr/bin/env python3
"""Load test script: push each endpoint to its limits against a running server.

Usage:
    # Against a local dev server:
    python load_test.py --base-url http://localhost:8000

    # Against Docker:
    python load_test.py --base-url http://localhost:8003

    # Custom concurrency and duration:
    python load_test.py --concurrency 50 --duration 30

This script is NOT a pytest test - it's a standalone tool that hits a live
server to find real-world limits (DB pool exhaustion, LLM queue saturation,
CPU/memory ceiling). It requires the user to provide a valid auth token.

Output is a CSV-friendly summary per endpoint showing:
    endpoint, requests, errors, p50_ms, p95_ms, p99_ms, max_ms

Requires: httpx[http2] (install: pip install httpx)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Endpoint:
    """Definition of an endpoint to load test."""
    name: str
    method: str
    path: str
    json_body: Optional[Dict[str, Any]] = None
    requires_auth: bool = True


@dataclass
class Result:
    """Aggregated result for one endpoint."""
    name: str
    latencies: List[float] = field(default_factory=list)
    errors: int = 0
    successes: int = 0


# ---------------------------------------------------------------------------
# Preset endpoints
# ---------------------------------------------------------------------------

DEFAULT_ENDPOINTS = [
    # Fast, no-auth
    Endpoint("health", "GET", "/health", requires_auth=False),
    # Auth
    Endpoint("auth/me", "GET", "/api/auth/me"),
    # Documents (requires a valid doc_id)
    Endpoint("documents/list", "GET", "/api/documents"),
    # Chat
    Endpoint("chat/sessions", "GET", "/api/chat/sessions"),
    # Metrics
    Endpoint("metrics", "GET", "/api/metrics"),
    Endpoint("metrics/summary", "GET", "/api/metrics/summary"),
]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def _run_endpoint(
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: Endpoint,
    headers: Dict[str, str],
    duration: int,
    result: Result,
) -> None:
    """Keep hitting one endpoint for `duration` seconds."""
    url = urljoin(base_url, endpoint.path)
    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        t0 = time.monotonic()
        try:
            resp = await client.request(
                endpoint.method, url, headers=headers,
                json=endpoint.json_body,
            )
            latency = (time.monotonic() - t0) * 1000
            result.latencies.append(latency)
            if 200 <= resp.status_code < 400:
                result.successes += 1
            else:
                result.errors += 1
        except Exception:
            result.errors += 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_report(results: List[Result]) -> None:
    """Print a formatted table of results."""
    print("\n" + "=" * 80)
    print(f"{'Endpoint':<25} {'Req':>6} {'Err':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'Max':>8}")
    print("-" * 80)

    for r in results:
        total = r.successes + r.errors
        if not r.latencies:
            print(f"{r.name:<25} {total:>6} {r.errors:>6} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>8}")
            continue
        latencies = sorted(r.latencies)
        n = len(latencies)
        p50 = latencies[int(n * 0.50)]
        p95 = latencies[int(n * 0.95)]
        p99 = latencies[min(int(n * 0.99), n - 1)]
        mx = latencies[-1]
        print(
            f"{r.name:<25} {total:>6} {r.errors:>6} "
            f"{p50:>7.1f} {p95:>7.1f} {p99:>7.1f} {mx:>7.1f}"
        )
    print("=" * 80)


async def main(
    base_url: str,
    token: Optional[str],
    doc_id: Optional[int],
    concurrency: int,
    duration: int,
) -> None:
    """Run the load test."""
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Build endpoint list
    endpoints = list(DEFAULT_ENDPOINTS)

    # Add doc-specific endpoints if doc_id provided
    if doc_id:
        endpoints.extend([
            Endpoint(f"documents/{doc_id}", "GET", f"/api/documents/{doc_id}"),
            Endpoint(f"documents/{doc_id}/pages", "GET", f"/api/documents/{doc_id}/pages"),
            Endpoint(f"documents/{doc_id}/progress", "PUT", f"/api/documents/{doc_id}/progress",
                     json_body={"page": 1, "position": 0.5}),
        ])

    print(f"Load testing {len(endpoints)} endpoints at {concurrency} concurrency for {duration}s")
    print(f"Base URL: {base_url}")
    print(f"Auth: {'yes' if token else 'no'}")
    print()

    results: List[Result] = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_connections=concurrency * 2),
    ) as client:
        all_tasks = []
        for ep in endpoints:
            r = Result(name=ep.name)
            results.append(r)
            # Fan out `concurrency` workers per endpoint
            for _ in range(concurrency):
                task = asyncio.create_task(
                    _run_endpoint(client, base_url, ep, headers, duration, r)
                )
                all_tasks.append(task)

        await asyncio.gather(*all_tasks)

    _print_report(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load test OpenNoteBox endpoints")
    parser.add_argument(
        "--base-url", default="http://localhost:8000",
        help="Base URL of the server",
    )
    parser.add_argument(
        "--token", default=None,
        help="Bearer auth token (skip for unauthenticated tests)",
    )
    parser.add_argument(
        "--doc-id", type=int, default=None,
        help="Document ID for document-specific endpoints",
    )
    parser.add_argument(
        "--concurrency", type=int, default=20,
        help="Number of concurrent workers per endpoint",
    )
    parser.add_argument(
        "--duration", type=int, default=15,
        help="Duration in seconds per endpoint",
    )
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.token, args.doc_id, args.concurrency, args.duration))
