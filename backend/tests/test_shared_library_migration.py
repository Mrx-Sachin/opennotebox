"""Exercise the shared-library migration's SQLite rebuild path."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

MigrationContext = pytest.importorskip("alembic.migration").MigrationContext
Operations = pytest.importorskip("alembic.operations").Operations


def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/f8d3c6a17b42_shared_library.py"
    spec = importlib.util.spec_from_file_location("shared_library_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _create_old_schema(connection):
    connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    connection.execute(text(
        "CREATE TABLE notebooks (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
        "name VARCHAR(100) NOT NULL, description VARCHAR(240), "
        "created_at DATETIME, updated_at DATETIME)"
    ))
    connection.execute(text(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, "
        "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
        "notebook_id INTEGER REFERENCES notebooks(id) ON DELETE SET NULL, "
        "title TEXT NOT NULL, original_url TEXT, format VARCHAR, file_path TEXT, "
        "file_size INTEGER, num_pages INTEGER, processing_status VARCHAR, "
        "layout_mode VARCHAR, created_at DATETIME, last_read_page INTEGER, "
        "last_read_position FLOAT, last_read_at DATETIME)"
    ))
    connection.execute(text(
        "CREATE TABLE summaries (id INTEGER PRIMARY KEY, "
        "document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE, "
        "mode VARCHAR NOT NULL, content TEXT NOT NULL)"
    ))
    connection.execute(text("INSERT INTO users VALUES (1)"))
    connection.execute(text(
        "INSERT INTO notebooks (id, user_id, name) VALUES (10, 1, 'Reading')"
    ))
    connection.execute(text(
        """INSERT INTO documents
        (id, user_id, notebook_id, title, original_url, format, file_path,
         file_size, num_pages, processing_status, layout_mode, created_at,
         last_read_page, last_read_position, last_read_at)
        VALUES (20, 1, 10, 'private-name.pdf', 'https://example.test/book',
                'pdf', '/asset.pdf', 123, 4, 'extracted', 'auto',
                '2026-01-01 00:00:00', 3, 0.5, '2026-01-02 00:00:00')"""
    ))
    connection.execute(text(
        "INSERT INTO summaries (id, document_id, mode, content) "
        "VALUES (30, 20, 'full', 'summary')"
    ))


def test_shared_library_migration_backfills_and_roundtrips(tmp_path):
    migration = _load_migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'shared-library.sqlite'}")
    try:
        with engine.begin() as connection:
            _create_old_schema(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            entry = connection.execute(text(
                "SELECT user_id, document_id, notebook_id, title, original_url, "
                "can_publish, last_read_page, last_read_position FROM library_entries"
            )).one()
            assert tuple(entry) == (1, 20, 10, "private-name.pdf", "https://example.test/book", 1, 3, 0.5)
            assert connection.execute(text(
                "SELECT user_id FROM summaries WHERE id=30"
            )).scalar_one() == 1
            assert connection.execute(text(
                "SELECT user_id FROM documents WHERE id=20"
            )).scalar_one() == 1
            document_user_fk = next(
                fk for fk in inspect(connection).get_foreign_keys("documents")
                if fk["constrained_columns"] == ["user_id"]
            )
            assert document_user_fk["options"]["ondelete"] == "SET NULL"
            assert connection.execute(text(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='document_topics'"
            )).scalar_one() == 1

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert connection.execute(text(
                "SELECT title, original_url, notebook_id, last_read_page "
                "FROM documents WHERE id=20"
            )).one() == ("private-name.pdf", "https://example.test/book", 10, 3)
            assert not inspect(connection).has_table("library_entries")
    finally:
        engine.dispose()


def test_shared_library_downgrade_rejects_multiple_members(tmp_path):
    migration = _load_migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'shared-library-guard.sqlite'}")
    try:
        with engine.begin() as connection:
            _create_old_schema(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(text("INSERT INTO users VALUES (2)"))
            connection.execute(text(
                "INSERT INTO library_entries (user_id, document_id, added_at, can_publish) "
                "VALUES (2, 20, CURRENT_TIMESTAMP, 0)"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="multi-user assets"):
                    migration.downgrade()
    finally:
        engine.dispose()
