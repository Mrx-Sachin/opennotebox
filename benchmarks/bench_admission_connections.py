"""Compare actual committed and candidate admission code on disposable Redis.

Measures lease overhead, not LLM inference or end-to-end chat latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
import types
import uuid
from pathlib import Path

import redis

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "596cd40c848a7c9bb761d0bcc051c9763a21c202"
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.services import llm_semaphore as candidate  # noqa: E402


def command(arguments):
    return subprocess.run(arguments, check=True, capture_output=True, text=True).stdout


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def summary(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


async def leases(module, count, concurrency):
    limit = asyncio.Semaphore(concurrency)

    async def request():
        async with limit:
            async with module.llm_semaphore():
                pass

    await asyncio.gather(*(request() for _ in range(count)))


def measure(module, control, scenario):
    connections = control.info("stats")["total_connections_received"]
    clients = control.info("clients")["connected_clients"]
    started = time.perf_counter()
    if scenario == "sequential":
        asyncio.run(leases(module, 100, 1))
    elif scenario == "burst30":
        asyncio.run(leases(module, 30, 30))
    else:
        for _ in range(10):
            asyncio.run(leases(module, 5, 1))
    elapsed = (time.perf_counter() - started) * 1000
    created = control.info("stats")["total_connections_received"] - connections
    assert control.zcard(settings.llm_admission_key_prefix + ":all") == 0
    assert control.zcard(settings.llm_admission_key_prefix + ":foreground") == 0
    assert control.info("clients")["connected_clients"] == clients
    assert not candidate._client_cache
    return {"elapsed_ms": elapsed, "connections_created": created, "errors": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_scratch/admission-results.json")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--baseline", default=BASELINE)
    args = parser.parse_args()
    if args.rounds < 5:
        parser.error("At least five alternating rounds are required")
    baseline = types.ModuleType("baseline_admission")
    source = command(["git", "-C", str(ROOT), "show",
                      args.baseline + ":backend/app/services/llm_semaphore.py"])
    exec(compile(source, "baseline_llm_semaphore.py", "exec"), baseline.__dict__)
    container = "notebox-admission-" + uuid.uuid4().hex[:10]
    port = free_port()
    command(["docker", "run", "--rm", "-d", "--name", container,
             "--memory=64m", "--memory-swap=64m", "--cpus=0.5",
             "-p", f"127.0.0.1:{port}:6379", "redis:7-alpine"])
    control = redis.Redis(host="127.0.0.1", port=port, socket_connect_timeout=0.2)
    report = {"baseline_commit": command(["git", "-C", str(ROOT), "rev-parse", args.baseline]).strip(),
              "python": platform.python_version(), "redis_py": redis.__version__,
              "load_average": os.getloadavg(), "cpu_count": os.cpu_count(),
              "rounds": args.rounds, "scope": "actual Lua lease overhead; excludes model calls",
              "redis_limits": {"memory_mib": 64, "cpus": 0.5}, "scenarios": {}}
    try:
        for _ in range(100):
            try:
                if control.ping():
                    break
            except redis.RedisError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Disposable Redis not ready")
        settings.llm_admission_redis_url = f"redis://127.0.0.1:{port}/0"
        settings.llm_admission_key_prefix = "benchmark:" + uuid.uuid4().hex
        settings.llm_max_concurrency = 24
        settings.llm_foreground_max_concurrency = 20
        settings.llm_foreground_wait_seconds = 10
        for scenario in ("sequential", "burst30", "ephemeral_loops"):
            samples = {"before": [], "after": []}
            for module in (baseline, candidate):
                measure(module, control, scenario)
            for round_index in range(args.rounds):
                order = ("before", "after") if round_index % 2 == 0 else ("after", "before")
                for variant in order:
                    module = baseline if variant == "before" else candidate
                    samples[variant].append(measure(module, control, scenario))
            report["scenarios"][scenario] = {
                "leases_per_round": 100 if scenario == "sequential" else 30 if scenario == "burst30" else 50,
                "samples": samples,
                "elapsed_ms": {variant: summary([s["elapsed_ms"] for s in values])
                               for variant, values in samples.items()},
            }
        report["status"] = "passed"
    finally:
        control.close()
        command(["docker", "rm", "-f", container])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
