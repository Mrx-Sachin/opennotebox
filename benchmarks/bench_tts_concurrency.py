#!/usr/bin/env python3
"""Concurrency and latency benchmark for same-account voice narration sessions.

Audits concurrent streaming TTS requests against an existing API deployment.
All sessions use the same demo user authentication token to measure same-account
concurrency without modifying services or provisioning new user accounts.

Validates:
1. Full-WAV response latency per concurrent session.
2. WAV audio format decoding, sample rate, and nonzero audio frames.
3. Health check responsiveness and status validation during active synthesis.
4. Session and audio cache release in finally blocks with 404 verification.
5. Capture of raw request metadata, cache IDs, and error logging.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import statistics
import sys
import time
import wave
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:3030"
DEFAULT_OUTPUT_PATH = "benchmarks/audit-results.json"
DEFAULT_SESSIONS = [1, 3, 5]

SENTENCES = [
    "All increases in the dataset size result in improved performance on all tasks.",
    "We chose an encoder-decoder Transformer architecture as it has been well validated to scale reliably.",
    "For feature normalization, we globally scale the input to be between minus one and one.",
    "Multitask learning provides robust transfer across speech recognition and translation benchmarks.",
    "The encoder processes this input representation with a small stem consisting of two convolution layers.",
]


def calculate_p95(values: List[float]) -> float:
    """Calculate the 95th percentile using the nearest-rank method."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    k = math.ceil(0.95 * len(sorted_vals)) - 1
    index = min(max(k, 0), len(sorted_vals) - 1)
    return sorted_vals[index]


def parse_user_counts(val: str) -> List[int]:
    """Parse and validate comma-separated concurrent session counts."""
    counts: List[int] = []
    for item in val.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        try:
            parsed = int(stripped)
            if parsed < 1:
                raise argparse.ArgumentTypeError(
                    f"Session count must be >= 1, got {parsed}"
                )
            counts.append(parsed)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Invalid integer session count: {stripped}"
            )
    if not counts:
        raise argparse.ArgumentTypeError("At least one session count required.")
    return counts


async def get_auth_token(client: httpx.AsyncClient, base_url: str) -> str:
    """Obtain authentication token for the demo user."""
    res = await client.post(
        f"{base_url}/api/auth/login",
        json={"username": "demo", "password": "demo123"},
    )
    res.raise_for_status()
    return res.json()["access_token"]


async def probe_health(client: httpx.AsyncClient, base_url: str) -> Dict[str, Any]:
    """Probe the FastAPI gateway health endpoint and validate its status."""
    t0 = time.perf_counter()
    try:
        resp = await client.get(f"{base_url}/health", timeout=5.0)
        latency_ms = (time.perf_counter() - t0) * 1000
        is_healthy = (
            resp.status_code == 200 and resp.json().get("status") == "healthy"
        )
        return {
            "latency_ms": latency_ms,
            "status_code": resp.status_code,
            "healthy": is_healthy,
            "error": None,
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        return {
            "latency_ms": latency_ms,
            "status_code": 0,
            "healthy": False,
            "error": str(exc),
        }


async def synthesize_single_session(
    client: httpx.AsyncClient,
    base_url: str,
    headers: Dict[str, str],
    session_index: int,
    sentence: str,
    speed: float = 1.0,
) -> Dict[str, Any]:
    """Execute streaming TTS synthesis for a single session and validate WAV."""
    session_id = f"bench-session-{session_index}-{int(time.time() * 1000)}"
    raw_request = {
        "text": f"{sentence} [nonce {time.time_ns()}]",
        "voice": "default",
        "speed": speed,
        "session_id": session_id,
    }

    cache_id: Optional[str] = None
    cached_header: Optional[str] = None
    audio_bytes: int = 0
    wav_valid: bool = False
    wav_sample_rate: int = 0
    wav_channels: int = 0
    wav_frames: int = 0
    wav_duration_s: float = 0.0
    wav_nonzero_samples: bool = False
    status_code: int = 0
    error_msg: Optional[str] = None

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{base_url}/api/tts/stream",
            headers=headers,
            json=raw_request,
            timeout=30.0,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        status_code = resp.status_code
        cache_id = resp.headers.get("x-tts-cache-id")
        cached_header = resp.headers.get("x-tts-cached")

        if status_code == 200:
            content = resp.content
            audio_bytes = len(content)
            try:
                with wave.open(io.BytesIO(content), "rb") as wf:
                    wav_channels = wf.getnchannels()
                    wav_sample_rate = wf.getframerate()
                    sampwidth = wf.getsampwidth()
                    wav_frames = wf.getnframes()
                    frames = wf.readframes(wav_frames)
                    wav_nonzero_samples = any(b != 0 for b in frames)
                    wav_duration_s = (
                        wav_frames / float(wav_sample_rate)
                        if wav_sample_rate > 0
                        else 0.0
                    )
                    wav_valid = (
                        wav_channels > 0
                        and wav_sample_rate > 0
                        and sampwidth > 0
                        and wav_frames > 0
                        and wav_nonzero_samples
                    )
            except Exception as wav_exc:
                wav_valid = False
                error_msg = f"WAV decode error: {wav_exc}"
        else:
            error_msg = f"HTTP {status_code}: {resp.text}"

    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        status_code = 0
        error_msg = str(exc)

    return {
        "session_index": session_index,
        "session_id": session_id,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "cache_id": cache_id,
        "cached_status": cached_header,
        "audio_bytes": audio_bytes,
        "wav_valid": wav_valid,
        "wav_channels": wav_channels,
        "wav_sample_rate": wav_sample_rate,
        "wav_frames": wav_frames,
        "wav_duration_s": wav_duration_s,
        "wav_nonzero_samples": wav_nonzero_samples,
        "sentence_chars": len(sentence),
        "raw_request": raw_request,
        "error": error_msg,
    }


async def cleanup_session(
    client: httpx.AsyncClient,
    base_url: str,
    headers: Dict[str, str],
    session_id: str,
    cache_id: Optional[str],
) -> Dict[str, Any]:
    """Release audio session and verify that the audio cache returns 404."""
    cleanup_info: Dict[str, Any] = {
        "release_status_code": 0,
        "release_response": None,
        "audio_check_status_code": None,
        "audio_verified_404": False,
        "cleanup_error": None,
    }
    try:
        parsed_cache_id = (
            int(cache_id)
            if cache_id and cache_id.isdigit()
            else None
        )
        rel_payload = {
            "session_id": session_id,
            "cache_ids": [parsed_cache_id] if parsed_cache_id is not None else [],
            "all_session": True,
        }
        rel_resp = await client.post(
            f"{base_url}/api/tts/release",
            headers=headers,
            json=rel_payload,
            timeout=10.0,
        )
        cleanup_info["release_status_code"] = rel_resp.status_code
        cleanup_info["release_response"] = (
            rel_resp.json() if rel_resp.status_code == 200 else rel_resp.text
        )

        if parsed_cache_id is not None:
            audio_check = await client.get(
                f"{base_url}/api/tts/audio/{parsed_cache_id}",
                headers=headers,
                timeout=5.0,
            )
            cleanup_info["audio_check_status_code"] = audio_check.status_code
            cleanup_info["audio_verified_404"] = (audio_check.status_code == 404)
        else:
            cleanup_info["audio_verified_404"] = True
    except Exception as exc:
        cleanup_info["cleanup_error"] = str(exc)

    return cleanup_info


async def run_concurrent_test(
    num_sessions: int,
    base_url: str,
) -> Dict[str, Any]:
    """Run concurrent narration sessions on a single authenticated account."""
    async with httpx.AsyncClient() as client:
        token = await get_auth_token(client, base_url)
        headers = {"Authorization": f"Bearer {token}"}
        print(f"\n--- Running benchmark with {num_sessions} concurrent session(s) ---")

        # Concurrently probe health every 50ms during active synthesis
        health_probes: List[Dict[str, Any]] = []

        async def monitor_health():
            while True:
                probe = await probe_health(client, base_url)
                health_probes.append(probe)
                await asyncio.sleep(0.05)

        monitor_task = asyncio.create_task(monitor_health())

        # Phase 1: Concurrently execute synthesis requests
        t_synthesis_start = time.perf_counter()
        synthesis_tasks = [
            synthesize_single_session(
                client=client,
                base_url=base_url,
                headers=headers,
                session_index=i + 1,
                sentence=SENTENCES[i % len(SENTENCES)],
            )
            for i in range(num_sessions)
        ]
        results = await asyncio.gather(*synthesis_tasks)
        synthesis_wall_time = time.perf_counter() - t_synthesis_start
        monitor_task.cancel()

        # Phase 2: In finally block, clean up each session and verify 404 side effect
        t_cleanup_start = time.perf_counter()
        cleanup_tasks = [
            cleanup_session(
                client=client,
                base_url=base_url,
                headers=headers,
                session_id=r["session_id"],
                cache_id=r["cache_id"],
            )
            for r in results
        ]
        cleanups = await asyncio.gather(*cleanup_tasks)
        cleanup_wall_time = time.perf_counter() - t_cleanup_start
        total_wall_time = synthesis_wall_time + cleanup_wall_time

        # Merge cleanup results into session records
        for r, c in zip(results, cleanups):
            r["cleanup"] = c

        successful = [
            r for r in results if r["status_code"] == 200 and r["wav_valid"]
        ]
        latencies = [r["latency_ms"] for r in successful]
        total_chars = sum(r["sentence_chars"] for r in successful)

        p50 = statistics.median(latencies) if latencies else 0.0
        p95 = calculate_p95(latencies)
        max_lat = max(latencies) if latencies else 0.0

        health_latencies = [p["latency_ms"] for p in health_probes if p["healthy"]]
        mean_health = statistics.mean(health_latencies) if health_latencies else 0.0
        max_health = max(health_latencies) if health_latencies else 0.0
        all_health_probes_ok = (
            len(health_probes) > 0 and all(p["healthy"] for p in health_probes)
        )

        all_cleanups_ok = all(
            r["cleanup"].get("audio_verified_404", False) for r in results
        )

        summary = {
            "concurrent_sessions": num_sessions,
            "account_mode": "same-account concurrent sessions (demo user)",
            "synthesis_wall_time_s": synthesis_wall_time,
            "cleanup_wall_time_s": cleanup_wall_time,
            "total_wall_time_with_cleanup_s": total_wall_time,
            "successful_sessions": len(successful),
            "failed_sessions": len(results) - len(successful),
            "latencies_ms": latencies,
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "max_latency_ms": max_lat,
            "chars_per_sec": (
                total_chars / synthesis_wall_time if synthesis_wall_time > 0 else 0.0
            ),
            "health_probed_route": "/health",
            "health_probe_route_note": "FastAPI gateway health check endpoint",
            "health_probe_total_count": len(health_probes),
            "health_probe_all_healthy": all_health_probes_ok,
            "health_probe_mean_ms": mean_health,
            "health_probe_max_ms": max_health,
            "cleanup_all_audio_verified_404": all_cleanups_ok,
            "session_results": results,
        }

        print(
            f"Synthesis time: {synthesis_wall_time:.2f}s | Total wall time: {total_wall_time:.2f}s | "
            f"Success: {len(successful)}/{num_sessions} | WAV Valid: {all(r['wav_valid'] for r in successful)}"
        )
        print(
            f"Full-WAV Response Latency: P50 = {p50:.1f}ms | P95 = {p95:.1f}ms | Max = {max_lat:.1f}ms"
        )
        print(
            f"Synthesis Throughput: {summary['chars_per_sec']:.1f} chars/sec (excludes cleanup)"
        )
        print(
            f"Health Probes (/health): count = {len(health_probes)} | "
            f"all_ok = {all_health_probes_ok} | mean = {mean_health:.1f}ms | max = {max_health:.1f}ms"
        )
        print(
            f"Cleanup verification (404 on release): {all_cleanups_ok}"
        )
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark same-account concurrent voice narration sessions."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Target backend base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help=f"Path to write benchmark audit results JSON (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--users",
        default="1,3,5",
        type=parse_user_counts,
        help="Comma-separated list of positive concurrent session counts (default: 1,3,5)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    session_counts = args.users

    print("================================================================")
    print("Voice Narration Same-Account Concurrent Session Audit Benchmark")
    print(f"Target: {args.base_url}")
    print(f"Session concurrency levels: {session_counts}")
    print(f"Output artifact: {args.output}")
    print("================================================================")

    audit_data: Dict[str, Any] = {
        "benchmark_metadata": {
            "title": "Voice Narration Same-Account Concurrency Audit",
            "base_url": args.base_url,
            "concurrency_levels": session_counts,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "probed_health_endpoint": "/health",
            "health_endpoint_note": (
                "Probes gateway responsiveness; does not isolate TTS worker event loop."
            ),
            "concurrency_scope": (
                "Concurrent requests originating from a single authenticated user account."
            ),
        },
        "results": {},
    }

    any_failures = False
    for n in session_counts:
        res = await run_concurrent_test(num_sessions=n, base_url=args.base_url)
        audit_data["results"][f"{n}_sessions"] = res
        if (
            res["failed_sessions"] > 0
            or not res["health_probe_all_healthy"]
            or not res["cleanup_all_audio_verified_404"]
        ):
            any_failures = True

    output_dir = os.path.dirname(os.path.abspath(args.output))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(audit_data, f, indent=2)

    print(f"\nAudit results successfully saved to {args.output}")

    if any_failures:
        print("FAIL: One or more benchmark runs experienced failures.")
        sys.exit(1)
    else:
        print("SUCCESS: All benchmark runs and cleanups verified successfully.")


if __name__ == "__main__":
    asyncio.run(main())
