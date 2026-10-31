"""Run disposable PostgreSQL/HTTP/proxy correctness and performance checks.

Run from the repository root with the existing Python environment. Docker is
required. Only containers created by this script are removed. Fixture JSON
is synthetic; no production service or database is queried or changed.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import importlib.metadata
import json
import os
import platform
import socket
import statistics
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import psutil
import psycopg
import yaml


ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/home/mrx/.local/venv/bin/python"
BASELINE = "596cd40c848a7c9bb761d0bcc051c9763a21c202"


def command(arguments, **kwargs):
    result = subprocess.run(arguments, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command {arguments[0]} failed: {result.stderr}")
    return result.stdout


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def summarize(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def schema_statements(source):
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_SCHEMA"
            for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise AssertionError("Retrieval schema missing")


def verify_indexes(database_url, rounds, baseline):
    """Compare identical warm datasets and execute the real migration."""
    before = schema_statements(command([
        "git", "show", f"{baseline}:backend/app/services/retrieval.py"
    ], cwd=ROOT))
    after = schema_statements((ROOT / "backend/app/services/retrieval.py").read_text())
    samples = {name: [] for name in ("before", "after")}
    plans = {}
    rows_by_variant = {}
    with psycopg.connect(database_url, autocommit=True) as connection:
        for variant, statements in (("before", before), ("after", after)):
            connection.execute(f"CREATE SCHEMA benchmark_{variant}")
            connection.execute(f"SET search_path TO benchmark_{variant}")
            for statement in statements:
                connection.execute(statement)
            connection.execute("""
                INSERT INTO chunk(document_id,page_number,chunk_index,text,emb)
                SELECT item % 20, item / 20, 0, repeat('resource evidence ',64),
                       CASE WHEN item % 97 = 0 THEN NULL ELSE decode('00','hex') END
                FROM generate_series(1,12000) item
            """)
            connection.execute("ANALYZE chunk")
        query = ("SELECT id FROM chunk WHERE document_id=7 AND emb IS NULL "
                 "ORDER BY page_number,chunk_index LIMIT 64")
        for variant in samples:
            connection.execute(f"SET search_path TO benchmark_{variant}")
            connection.execute(query).fetchall()
        for round_index in range(rounds):
            order = ("before", "after") if round_index % 2 == 0 else ("after", "before")
            for variant in order:
                connection.execute(f"SET search_path TO benchmark_{variant}")
                started = time.perf_counter()
                for repetition in range(20):
                    rows = connection.execute(query).fetchall()
                    assert len(rows) == 6
                samples[variant].append((time.perf_counter() - started) * 1000 / 20)
                rows_by_variant[variant] = rows
        assert rows_by_variant["before"] == rows_by_variant["after"]
        for variant in samples:
            connection.execute(f"SET search_path TO benchmark_{variant}")
            plans[variant] = connection.execute(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query
            ).fetchone()[0]
        connection.execute("SET search_path TO benchmark_before")
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine

        migration_path = next((ROOT / "backend/alembic/versions").glob("eff000*.py"))
        specification = importlib.util.spec_from_file_location("benchmark_migration", migration_path)
        migration = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration)
        engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://"))
        with engine.begin() as migration_connection:
            migration_connection.exec_driver_sql("SET search_path TO benchmark_before")
            with Operations.context(MigrationContext.configure(migration_connection)):
                migration.upgrade()
                migration.downgrade()
                migration.upgrade()
                migration.upgrade()
        engine.dispose()
        definitions = connection.execute(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='benchmark_before'"
        ).fetchall()
        assert not any(name == "idx_chunk_doc" for name, definition in definitions)
        assert any(name == "idx_chunk_unembedded" for name, definition in definitions)
    return {"samples_ms": samples, "summary_ms": {name: summarize(values) for name, values
             in samples.items()}, "plans": plans, "rows_per_query": 6,
            "migration_upgrade_downgrade_replay": "passed"}


async def verify_http(base_url, credentials, rounds):
    """Alternate full/light responses over real HTTP for independent users."""
    results = {}
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=40)) as client:
        account = credentials["accounts"][0]
        headers = {"Authorization": f"Bearer {account['token']}", "Accept-Encoding": "identity"}
        document_id = credentials["documents"][0]
        path = f"/api/documents/{document_id}/pages"
        full = await client.get(base_url + path, headers=headers)
        baseline = await client.get(base_url + "/baseline" + path, headers=headers)
        light = await client.get(base_url + path + "?lightweight=true", headers=headers)
        assert baseline.status_code == full.status_code == light.status_code == 200
        assert baseline.json() == full.json()
        results["profiles"] = [
            (await client.get(base_url + f"/api/benchmark/profile/{variant}")).json()
            for variant in ("before", "after")
        ]
        full_pages, light_pages = full.json(), light.json()
        assert len(full_pages) == len(light_pages) == 100
        for full_page, light_page in zip(full_pages, light_pages):
            assert light_page == {key: full_page[key] for key in light_page}
            assert "bounding_boxes" in full_page and "bounding_boxes" not in light_page
        query_before = (await client.get(base_url + "/api/benchmark/query-count")).json()["queries"]
        checked = await client.get(base_url + path + "?lightweight=true", headers=headers)
        query_after = (await client.get(base_url + "/api/benchmark/query-count")).json()["queries"]
        assert checked.status_code == 200
        assert query_after - query_before <= 4
        for concurrency in (1, 15, 30):
            samples = {name: [] for name in ("before", "after")}
            errors = 0
            for round_index in range(rounds):
                order = ("before", "after") if round_index % 2 == 0 else ("after", "before")
                for variant in order:
                    async def request(account_index, variant=variant):
                        user = credentials["accounts"][account_index]
                        doc = credentials["documents"][account_index % 3]
                        suffix = "?lightweight=true" if variant == "after" else ""
                        prefix = "/baseline" if variant == "before" else ""
                        response = await client.get(
                            f"{base_url}{prefix}/api/documents/{doc}/pages{suffix}",
                            headers={"Authorization": f"Bearer {user['token']}",
                                     "Accept-Encoding": "identity"},
                        )
                        if response.status_code != 200:
                            return False
                        pages = response.json()
                        assert len(pages) == 100 and pages[-1]["page_number"] == 100
                        assert pages[0]["document_id"] == doc
                        assert "Evidence" in pages[0]["content"]
                        return True

                    started = time.perf_counter()
                    successes = await asyncio.gather(*(request(index) for index in range(concurrency)))
                    samples[variant].append((time.perf_counter() - started) * 1000)
                    errors += successes.count(False)
            assert errors == 0
            results[str(concurrency)] = {
                "batch_latency_ms": samples,
                "summary_ms": {name: summarize(values) for name, values in samples.items()},
                "requests_per_variant": concurrency * rounds, "errors": errors,
            }
        results["payload_bytes"] = {"full": len(full.content), "lightweight": len(light.content)}
        results["lightweight_query_count"] = query_after - query_before
        response = await client.get(base_url + path + "?lightweight=true&offset=98&limit=2", headers=headers)
        assert [page["page_number"] for page in response.json()] == [99, 100]
        geometry_document = credentials["geometry_document"]
        response = await client.get(base_url + f"/api/documents/{geometry_document}/page/1", headers=headers)
        assert response.status_code == 200 and len(response.json()["bounding_boxes"]) == 250
    return results


async def verify_proxy(base_url, credentials):
    import websockets

    async with httpx.AsyncClient(timeout=60) as client:
        path = f"/api/documents/{credentials['documents'][0]}/pages?lightweight=true"
        headers = {"Authorization": f"Bearer {credentials['accounts'][0]['token']}"}
        plain = await client.get(base_url + path, headers={**headers, "Accept-Encoding": "identity"})
        compressed = await client.get(base_url + path, headers={**headers, "Accept-Encoding": "gzip"})
        assert plain.status_code == compressed.status_code == 200
        assert compressed.headers.get("content-encoding") == "gzip"
        assert plain.json() == compressed.json()
        started = time.perf_counter()
        async with client.stream("GET", base_url + "/api/benchmark/events",
                                 headers={"Accept-Encoding": "gzip"}) as response:
            assert "content-encoding" not in response.headers
            arrivals = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    arrivals.append((time.perf_counter() - started, line))
            assert len(arrivals) == 2
            assert arrivals[1][0] - arrivals[0][0] > 0.1
        audio = await client.get(base_url + "/api/benchmark/audio", headers={"Accept-Encoding": "gzip"})
        assert audio.status_code == 200 and "content-encoding" not in audio.headers
        assert audio.content.startswith(b"RIFF")
    async with websockets.connect(base_url.replace("http://", "ws://") + "/ws/benchmark") as websocket:
        await websocket.send("verify-upgrade")
        assert await websocket.recv() == "verify-upgrade"
    return {"json_identity_bytes": plain.num_bytes_downloaded,
            "json_gzip_bytes": compressed.num_bytes_downloaded,
            "sse_arrivals": arrivals, "audio_uncompressed": True, "websocket_echo": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_scratch/resource-verification")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--baseline", default=BASELINE)
    arguments = parser.parse_args()
    if arguments.rounds < 5:
        parser.error("At least five alternating rounds are required")
    arguments.output.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:10]
    postgres_name = f"notebox-perf-pg-{run_id}"
    proxy_name = f"notebox-perf-proxy-{run_id}"
    postgres_port, api_port, proxy_port = free_port(), free_port(), free_port()
    database_url = f"postgresql://benchmark:benchmark@127.0.0.1:{postgres_port}/benchmark"
    environment = {**os.environ, "DATABASE_URL": database_url.replace("postgresql://", "postgresql+psycopg://"),
                   "JWT_SECRET": "disposable-benchmark-secret-with-more-than-32-chars",
                   "DATA_DIR": str(arguments.output / "data"),
                   "BENCHMARK_CREDENTIALS": str(arguments.output / "credentials.json"),
                   "BENCHMARK_BASELINE": arguments.baseline,
                   "PYTHONPATH": str(ROOT / "backend") + os.pathsep + str(ROOT / "benchmarks")}
    server = None
    created = []
    report = {"rounds": arguments.rounds,
              "baseline_commit": command(["git", "rev-parse", arguments.baseline], cwd=ROOT).strip(),
              "load_average": os.getloadavg(),
              "cpu_count": os.cpu_count(),
              "fixture": "30 accounts, 3 text documents with 100 pages each and null geometry; 1 PDF geometry probe",
              "versions": {"python": platform.python_version(),
                           **{name: importlib.metadata.version(name) for name in
                              ("fastapi", "pydantic", "sqlalchemy", "psycopg", "uvicorn")},
                           "postgres_image": "pgvector/pgvector:pg16",
                           "traefik_image": "traefik:v3.7.14"},
              "scope": "real document routes, synthetic data; excludes LLM/TTS/parsing capacity"}
    try:
        command(["docker", "run", "-d", "--name", postgres_name, "--memory=512m", "--memory-swap=512m",
                 "--cpus=1", "-p", f"127.0.0.1:{postgres_port}:5432", "-e", "POSTGRES_USER=benchmark",
                 "-e", "POSTGRES_PASSWORD=benchmark", "-e", "POSTGRES_DB=benchmark", "pgvector/pgvector:pg16"])
        created.append(postgres_name)
        for attempt in range(100):
            try:
                with psycopg.connect(database_url):
                    break
            except psycopg.OperationalError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        report["indexes"] = verify_indexes(database_url, arguments.rounds, arguments.baseline)
        command([PYTHON, str(ROOT / "benchmarks/resource_fixture.py")], cwd=ROOT, env=environment)
        credentials = json.loads(Path(environment["BENCHMARK_CREDENTIALS"]).read_text())
        with (arguments.output / "api.log").open("w") as log:
            server = subprocess.Popen([PYTHON, "-m", "uvicorn", "resource_fixture:create_app", "--factory",
                                       "--host", "127.0.0.1", "--port", str(api_port), "--no-access-log"],
                                      cwd=ROOT, env=environment, stdout=log, stderr=log)
            api_url = f"http://127.0.0.1:{api_port}"
            for attempt in range(100):
                try:
                    if httpx.get(api_url + "/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if server.poll() is not None:
                    raise RuntimeError("Fixture API failed; inspect api.log")
                time.sleep(0.1)
            else:
                raise RuntimeError("Fixture API not ready")
            report["http"] = asyncio.run(verify_http(api_url, credentials, arguments.rounds))
            report["api_rss_after_http_bytes"] = psutil.Process(server.pid).memory_info().rss
            routes = yaml.safe_load((ROOT / "traefik/preview.yml").read_text())
            routes["http"]["services"]["api"]["loadBalancer"]["servers"] = [{"url": api_url}]
            routes["http"]["services"]["frontend"]["loadBalancer"]["servers"] = [{"url": api_url}]
            routes_path = arguments.output / "routes.yml"
            routes_path.write_text(yaml.safe_dump(routes))
            command(["docker", "run", "-d", "--name", proxy_name, "--network=host", "--memory=128m",
                     "--memory-swap=128m", "--cpus=0.5", "-v", f"{routes_path.resolve()}:/etc/traefik/routes.yml:ro",
                     "traefik:v3.7.14", f"--entrypoints.weblocal.address=127.0.0.1:{proxy_port}",
                     "--providers.file.filename=/etc/traefik/routes.yml"])
            created.append(proxy_name)
            proxy_url = f"http://127.0.0.1:{proxy_port}"
            for attempt in range(100):
                try:
                    if httpx.get(proxy_url + "/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            report["proxy"] = asyncio.run(verify_proxy(proxy_url, credentials))
        report["status"] = "passed"
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=15)
        for name in reversed(created):
            command(["docker", "rm", "-f", "-v", name])
        Path(environment["BENCHMARK_CREDENTIALS"]).unlink(missing_ok=True)
        (arguments.output / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
