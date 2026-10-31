"""Verify layout migration and 30 concurrent claims on disposable PostgreSQL.

Runs real document handlers and the real task body with a real Docling model.
The enqueue function records dispatches, so no production broker is contacted.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def command(arguments: list[str]) -> str:
    return subprocess.check_output(arguments, text=True, stderr=subprocess.STDOUT).strip()


def verify(output: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    name = f"verify-layout-{uuid.uuid4().hex[:12]}"
    container_created = False
    with tempfile.TemporaryDirectory(prefix="verify-layout-") as temporary:
        try:
            command(["docker", "run", "--detach", "--name", name, "--memory", "512m",
                     "--memory-swap", "512m", "--cpus", "1", "-p", f"127.0.0.1:{port}:5432",
                     "-e", "POSTGRES_PASSWORD=disposable", "-e", "POSTGRES_DB=layout_test",
                     "postgres:16-alpine"])
            container_created = True
            import psycopg

            url = f"postgresql://postgres:disposable@127.0.0.1:{port}/layout_test"
            for attempt in range(100):
                try:
                    with psycopg.connect(url):
                        break
                except psycopg.OperationalError:
                    if attempt == 99:
                        raise
                    time.sleep(0.2)
            os.environ.update({"DATABASE_URL": url.replace("postgresql://", "postgresql+psycopg://"),
                               "DATA_DIR": temporary, "JWT_SECRET": "disposable-layout-test-secret-32-chars",
                               "HF_HUB_OFFLINE": "1"})
            sys.path.insert(0, str(ROOT / "backend"))
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            from app.database import Base, SessionLocal, engine
            from app.models import Document, ExtractedText, User
            from app.routers.documents import request_document_layout
            from app.tasks import pdf_tasks
            from sqlalchemy import text

            migration_path = next((ROOT / "backend/alembic/versions").glob("a91c2e7d408b*.py"))
            spec = importlib.util.spec_from_file_location("optional_layout_migration", migration_path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with engine.begin() as connection:
                connection.execute(text("CREATE SCHEMA migration_check"))
                connection.execute(text("SET LOCAL search_path TO migration_check"))
                connection.execute(text("CREATE TABLE documents(id INT PRIMARY KEY, format TEXT, num_pages INT)"))
                connection.execute(text("CREATE TABLE extracted_text(document_id INT, layout_regions JSON, narration_exclusions JSON)"))
                connection.execute(text("INSERT INTO documents VALUES (1,'pdf',1),(2,'pdf',1),(3,'pdf',1)"))
                connection.execute(text("INSERT INTO extracted_text VALUES (1,'[]','[]'),(2,'null','null'),(3,NULL,NULL)"))
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                    assert connection.execute(text("SELECT layout_status FROM documents ORDER BY id")).scalars().all() == ["ready", "not_requested", "not_requested"]
                    migration.downgrade()
                    migration.upgrade()
            Base.metadata.create_all(engine)
            fixture = ROOT / "e2e/fixtures/docling-narration.pdf"
            parsed = pdf_tasks.pdf_parser.parse_pdf(str(fixture), use_ocr=False)
            assert all(not page["layout_regions"] for page in parsed["pages"])
            with SessionLocal() as session:
                user = User(username="layout-verifier", password_hash="unused")
                session.add(user)
                session.flush()
                doc = Document(user_id=user.id, title=fixture.name, format="pdf", file_path=str(fixture),
                               file_size=fixture.stat().st_size, num_pages=parsed["num_pages"], processing_status="extracted")
                session.add(doc)
                session.flush()
                pdf_tasks._replace_extracted_pages(session, doc.id, parsed["pages"])
                session.commit()
                doc_id, user_id = doc.id, user.id
                before = [(page.id, page.content, page.bounding_boxes) for page in session.query(ExtractedText).order_by(ExtractedText.page_number)]
            dispatches = []
            lock = threading.Lock()

            def record_dispatch(**kwargs):
                with lock:
                    dispatches.append(kwargs)

            def request(_):
                with SessionLocal() as session:
                    return request_document_layout(doc_id, session.get(User, user_id), session)

            with patch.object(pdf_tasks.analyze_document_layout, "apply_async", side_effect=record_dispatch):
                with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
                    responses = list(executor.map(request, range(30)))
                assert len(dispatches) == 1, dispatches
                assert all(response["status"] == "processing" for response in responses)
                task_id = dispatches[0]["task_id"]
                started = time.perf_counter()
                task_result = pdf_tasks.analyze_document_layout.run(doc_id, task_id)
                duration = time.perf_counter() - started
                assert task_result["status"] == "ready", task_result
                assert request(0)["status"] == "ready"
                assert len(dispatches) == 1
                assert pdf_tasks.analyze_document_layout.run(doc_id, task_id)["status"] == "ignored"
            with SessionLocal() as session:
                pages = session.query(ExtractedText).order_by(ExtractedText.page_number).all()
                assert [(page.id, page.content, page.bounding_boxes) for page in pages] == before
                regions = sum(len(page.layout_regions or []) for page in pages)
                excluded = sum(len(page.narration_exclusions or []) for page in pages)
                assert regions > 0 and excluded > 0
                assert session.get(Document, doc_id).processing_status == "extracted"
            report = {"failures": 0, "database": "disposable PostgreSQL 16", "database_memory_limit_mib": 512,
                      "database_cpu_limit": 1, "concurrent_requests": 30, "queued_builds": len(dispatches),
                      "migration_upgrade_downgrade_upgrade": "passed", "legacy_empty_result_backfill": "passed",
                      "actual_layout_task_seconds_single_run": duration, "regions": regions,
                      "exclusion_ranges": excluded, "text_and_geometry_unchanged": True,
                      "duplicate_task_ignored": True,
                      "scope": "Actual PostgreSQL handler concurrency and real CPU Docling task body. "
                               "Enqueue recorded in memory, not a broker/worker integration or 30-user throughput test."}
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            engine.dispose()
        finally:
            if container_created:
                command(["docker", "rm", "--force", name])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/results/optional-layout-integration.json")
    verify(parser.parse_args().output)
