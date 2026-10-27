"""Validate sharing on a disposable PostgreSQL copy of the preview database."""
from pathlib import Path
import subprocess


REPO = Path(__file__).resolve().parent.parent
STATE = Path.home() / ".local/state/deepread/preview"
DATABASE = "deepread_shared_migration_check"
COMPOSE = [
    "docker", "compose", "--env-file", str(STATE / "preview.env"),
    "-f", "docker-compose.preview.yml",
]


def postgres_sql(sql):
    subprocess.run(
        COMPOSE + ["exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1",
                   "-U", "deepread_preview", "-d", "postgres", "-c", sql],
        cwd=REPO, check=True,
    )


SCRIPT = r'''
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

url = make_url(os.environ["DATABASE_URL"]).set(database="deepread_shared_migration_check")
os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
engine = create_engine(url)
with engine.connect() as connection:
    original = connection.execute(text("SELECT id, user_id, title, notebook_id, last_read_page FROM documents ORDER BY id")).all()
subprocess.run(["alembic", "upgrade", "head"], check=True)
with engine.connect() as connection:
    entries = connection.execute(text("SELECT document_id, user_id, title, notebook_id, last_read_page FROM library_entries ORDER BY document_id")).all()
    assert original == entries, "Migration changed personal document state"
    missing = connection.execute(text("SELECT count(*) FROM summaries s JOIN documents d ON d.id=s.document_id WHERE s.user_id IS DISTINCT FROM d.user_id")).scalar_one()
    assert missing == 0, "Legacy summaries were not assigned to their user"
subprocess.run(["alembic", "downgrade", "b4e8f2a61c90"], check=True)
subprocess.run(["alembic", "upgrade", "head"], check=True)

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Document, LibraryEntry, Notebook, User
from app.services.auth_service import create_access_token

with SessionLocal() as db:
    publisher = User(username="migration.check.publisher", password_hash="unused")
    reader = User(username="migration.check.reader", password_hash="unused")
    db.add_all([publisher, reader])
    db.flush()
    doc = Document(user_id=publisher.id, title="Private uploader filename.pdf", format="pdf", file_path="/tmp/migration-check.pdf", file_size=1, num_pages=1, processing_status="completed", is_shared=True, shared_title="Concurrent catalog fixture", shared_by_user_id=publisher.id)
    notebook = Notebook(user_id=reader.id, name="Concurrency check")
    db.add_all([doc, notebook])
    db.commit()
    doc_id, reader_id, notebook_id = doc.id, reader.id, notebook.id
token = create_access_token({"sub": str(reader_id)})
headers = {"Authorization": f"Bearer {token}"}
with TestClient(app) as client:
    assert client.get(f"/api/documents/{doc_id}", headers=headers).status_code == 404
    def add(_):
        result = client.post("/api/shared-library/add", headers=headers, json={"document_ids": [doc_id], "notebook_id": notebook_id})
        assert result.status_code == 200, result.text
        return result.json()["added"]
    with ThreadPoolExecutor(max_workers=12) as pool:
        counts = list(pool.map(add, range(12)))
    assert sum(counts) == 1, "Concurrent additions created duplicate memberships"
    response = client.get(f"/api/documents/{doc_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Concurrent catalog fixture"
    assert response.json()["notebook_id"] == notebook_id
with SessionLocal() as db:
    assert db.query(LibraryEntry).filter_by(document_id=doc_id).count() == 2
    assert db.query(Document).filter_by(id=doc_id).count() == 1
print("PostgreSQL migration round trip, state preservation and 12 concurrent catalog additions passed")
'''

try:
    postgres_sql(f"CREATE DATABASE {DATABASE}")
    with (STATE / "testing/database-before-shared-library.dump").open("rb") as backup:
        subprocess.run(
            COMPOSE + ["exec", "-T", "postgres", "pg_restore", "-U",
                       "deepread_preview", "-d", DATABASE, "--no-owner", "--exit-on-error"],
            cwd=REPO, stdin=backup, check=True,
        )
    subprocess.run(
        COMPOSE + ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "api", "-"],
        cwd=REPO, input=SCRIPT, text=True, check=True,
    )
finally:
    postgres_sql(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)")
