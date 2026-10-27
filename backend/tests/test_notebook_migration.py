"""Check the notebook migration against SQLite's table-rebuild path."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

MigrationContext = pytest.importorskip(
    "alembic.migration", reason="Alembic is required to exercise schema migration"
).MigrationContext
Operations = pytest.importorskip("alembic.operations").Operations


def test_notebook_migration_preserves_existing_documents(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/b4e8f2a61c90_notebooks.py"
    spec = importlib.util.spec_from_file_location("notebook_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            connection.execute(text(
                "CREATE TABLE documents (id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL REFERENCES users(id))"
            ))
            connection.execute(text("INSERT INTO users VALUES (1)"))
            connection.execute(text("INSERT INTO documents VALUES (1, 1)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert connection.execute(text(
                    "SELECT notebook_id FROM documents WHERE id=1"
                )).scalar() is None
                connection.execute(text(
                    "INSERT INTO notebooks (id, user_id, name) VALUES (1, 1, 'Research')"
                ))
                connection.execute(text("UPDATE documents SET notebook_id=1 WHERE id=1"))
                migration.downgrade()
                assert connection.execute(text("SELECT count(*) FROM documents")).scalar() == 1
    finally:
        engine.dispose()
