"""Tests for retrieval chunk schema, index optimizations, and migration."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.services import retrieval

MigrationContext = pytest.importorskip("alembic.migration").MigrationContext
Operations = pytest.importorskip("alembic.operations").Operations


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "eff000e147f2_drop_redundant_chunk_idx_and_add_partial_unembedded_idx.py"
    )
    module_spec = importlib.util.spec_from_file_location(
        "drop_redundant_chunk_idx_migration", migration_path
    )
    migration_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(migration_module)
    return migration_module


def test_migration_upgrade_and_downgrade(tmp_path):
    migration = _load_migration()
    sqlite_file = tmp_path / "test_migration.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE chunk ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "document_id INTEGER NOT NULL, "
                    "page_number INTEGER NOT NULL, "
                    "chunk_index INTEGER NOT NULL, "
                    "text TEXT NOT NULL, "
                    "emb BLOB, "
                    "CONSTRAINT uq_chunk_document_page_index "
                    "UNIQUE(document_id, page_number, chunk_index)"
                    ")"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX idx_chunk_doc "
                    "ON chunk(document_id, page_number, chunk_index)"
                )
            )

            inspector = inspect(connection)
            initial_indexes = {
                idx["name"] for idx in inspector.get_indexes("chunk")
            }
            assert "idx_chunk_doc" in initial_indexes

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            inspector = inspect(connection)
            upgraded_indexes = {
                idx["name"] for idx in inspector.get_indexes("chunk")
            }
            assert "idx_chunk_doc" not in upgraded_indexes
            assert "idx_chunk_unembedded" in upgraded_indexes

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()

            inspector = inspect(connection)
            downgraded_indexes = {
                idx["name"] for idx in inspector.get_indexes("chunk")
            }
            assert "idx_chunk_unembedded" not in downgraded_indexes
            assert "idx_chunk_doc" in downgraded_indexes

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            inspector = inspect(connection)
            reupgraded_indexes = {
                idx["name"] for idx in inspector.get_indexes("chunk")
            }
            assert "idx_chunk_doc" not in reupgraded_indexes
            assert "idx_chunk_unembedded" in reupgraded_indexes
    finally:
        engine.dispose()


def test_migration_safe_when_chunk_table_missing(tmp_path):
    migration = _load_migration()
    sqlite_file = tmp_path / "test_missing_chunk.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.downgrade()
    finally:
        engine.dispose()


def test_migration_idempotent_when_indexes_already_updated(tmp_path):
    migration = _load_migration()
    sqlite_file = tmp_path / "test_idempotent.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE chunk ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "document_id INTEGER NOT NULL, "
                    "page_number INTEGER NOT NULL, "
                    "chunk_index INTEGER NOT NULL, "
                    "text TEXT NOT NULL, "
                    "emb BLOB"
                    ")"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()

            inspector = inspect(connection)
            indexes = {idx["name"] for idx in inspector.get_indexes("chunk")}
            assert "idx_chunk_unembedded" in indexes
            assert "idx_chunk_doc" not in indexes

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.downgrade()

            inspector = inspect(connection)
            indexes_down = {
                idx["name"] for idx in inspector.get_indexes("chunk")
            }
            assert "idx_chunk_unembedded" not in indexes_down
            assert "idx_chunk_doc" in indexes_down
    finally:
        engine.dispose()


def test_ensure_schema_creates_partial_index_and_omits_idx_chunk_doc(tmp_path):
    sqlite_file = tmp_path / "test_ensure.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        retrieval.ensure_schema(session)

        inspector = inspect(engine)
        indexes = {idx["name"] for idx in inspector.get_indexes("chunk")}
        assert "idx_chunk_unembedded" in indexes
        assert "idx_chunk_doc" not in indexes
        assert "uq_chunk_document_page_index" in indexes
    finally:
        session.close()
        engine.dispose()


def test_ensure_schema_repeat_initialization_is_replay_safe(tmp_path):
    sqlite_file = tmp_path / "test_repeat.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        for _ in range(3):
            retrieval.ensure_schema(session)

        inspector = inspect(engine)
        indexes = {idx["name"] for idx in inspector.get_indexes("chunk")}
        assert "idx_chunk_unembedded" in indexes
        assert "idx_chunk_doc" not in indexes
    finally:
        session.close()
        engine.dispose()


def test_ensure_schema_does_not_recreate_dropped_idx_chunk_doc(tmp_path):
    migration = _load_migration()
    sqlite_file = tmp_path / "test_no_recreate.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE chunk ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "document_id INTEGER NOT NULL, "
                    "page_number INTEGER NOT NULL, "
                    "chunk_index INTEGER NOT NULL, "
                    "text TEXT NOT NULL, "
                    "emb BLOB, "
                    "CONSTRAINT uq_chunk_document_page_index "
                    "UNIQUE(document_id, page_number, chunk_index)"
                    ")"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX idx_chunk_doc "
                    "ON chunk(document_id, page_number, chunk_index)"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

        session_factory = sessionmaker(bind=engine)
        session = session_factory()
        try:
            retrieval.ensure_schema(session)
            inspector = inspect(engine)
            indexes = {idx["name"] for idx in inspector.get_indexes("chunk")}
            assert "idx_chunk_doc" not in indexes
            assert "idx_chunk_unembedded" in indexes
        finally:
            session.close()
    finally:
        engine.dispose()


def test_list_unembedded_chunk_ids_query_results_and_ordering(tmp_path):
    sqlite_file = tmp_path / "test_query.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        retrieval.ensure_schema(session)

        session.execute(
            text(
                "INSERT INTO chunk (document_id, page_number, chunk_index, text, emb) "
                "VALUES "
                "(1, 2, 1, 'doc1 page2 chunk1', NULL), "
                "(1, 1, 0, 'doc1 page1 chunk0', NULL), "
                "(1, 1, 1, 'doc1 page1 chunk1', :emb), "
                "(1, 2, 0, 'doc1 page2 chunk0', NULL), "
                "(2, 1, 0, 'doc2 page1 chunk0', NULL)"
            ),
            {"emb": b"embedded_vector_bytes"},
        )
        session.commit()

        unembedded = retrieval.list_unembedded_chunk_ids(
            session, doc_id=1, limit=10
        )
        assert len(unembedded) == 3

        rows = session.execute(
            text(
                "SELECT id, page_number, chunk_index FROM chunk "
                "WHERE id IN (:c0, :c1, :c2) ORDER BY page_number, chunk_index"
            ),
            {"c0": unembedded[0], "c1": unembedded[1], "c2": unembedded[2]},
        ).all()
        assert [r[0] for r in rows] == unembedded

        first_two = retrieval.list_unembedded_chunk_ids(
            session, doc_id=1, limit=2
        )
        assert first_two == unembedded[:2]

        doc2_unembedded = retrieval.list_unembedded_chunk_ids(
            session, doc_id=2, limit=10
        )
        assert len(doc2_unembedded) == 1

        doc3_unembedded = retrieval.list_unembedded_chunk_ids(
            session, doc_id=3, limit=10
        )
        assert doc3_unembedded == []

        explain_rows = session.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT id FROM chunk WHERE document_id = 1 AND emb IS NULL "
                "ORDER BY page_number, chunk_index LIMIT 64"
            )
        ).all()
        plan_text = " ".join(str(row) for row in explain_rows)
        assert "idx_chunk_unembedded" in plan_text
    finally:
        session.close()
        engine.dispose()


def test_unique_constraint_prevent_duplicates(tmp_path):
    sqlite_file = tmp_path / "test_unique.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        retrieval.ensure_schema(session)
        session.execute(
            text(
                "INSERT INTO chunk (document_id, page_number, chunk_index, text) "
                "VALUES (1, 1, 0, 'first chunk')"
            )
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO chunk (document_id, page_number, chunk_index, text) "
                    "VALUES (1, 1, 0, 'duplicate chunk')"
                )
            )
            session.commit()
    finally:
        session.close()
        engine.dispose()
