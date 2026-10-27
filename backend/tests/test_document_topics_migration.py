"""Tests for document_topics table migration and topic synchronization."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Document, DocumentTopic

MigrationContext = pytest.importorskip("alembic.migration").MigrationContext
Operations = pytest.importorskip("alembic.operations").Operations


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "2c8a9f4e1b3d_document_topics_table.py"
    )
    module_spec = importlib.util.spec_from_file_location(
        "document_topics_migration", migration_path
    )
    migration_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(migration_module)
    return migration_module


def test_migration_upgrade_and_downgrade_cleanly(tmp_path):
    migration = _load_migration()
    sqlite_file = tmp_path / "test_migration.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE documents ("
                    "id INTEGER PRIMARY KEY, "
                    "is_shared BOOLEAN, "
                    "shared_at DATETIME, "
                    "topics JSON"
                    ")"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            inspector = inspect(connection)
            table_names = inspector.get_table_names()
            assert "document_topics" in table_names

            columns = {
                column_info["name"]: column_info
                for column_info in inspector.get_columns("document_topics")
            }
            assert "document_id" in columns
            assert "topic" in columns

            primary_key = inspector.get_pk_constraint("document_topics")
            assert set(primary_key["constrained_columns"]) == {
                "document_id",
                "topic",
            }

            topic_indexes = [
                index_data["name"]
                for index_data in inspector.get_indexes("document_topics")
            ]
            assert "ix_document_topics_topic_document" in topic_indexes

            document_indexes = [
                index_data["name"]
                for index_data in inspector.get_indexes("documents")
            ]
            assert "ix_documents_shared_catalog" in document_indexes

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()

            inspector.clear_cache()
            table_names_after_downgrade = inspector.get_table_names()
            assert "document_topics" not in table_names_after_downgrade

            document_indexes_after_downgrade = [
                index_data["name"]
                for index_data in inspector.get_indexes("documents")
            ]
            assert (
                "ix_documents_shared_catalog"
                not in document_indexes_after_downgrade
            )
    finally:
        engine.dispose()


def test_migration_safe_with_partially_present_objects(tmp_path):
    migration = _load_migration()
    sqlite_file = tmp_path / "test_partial.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE documents ("
                    "id INTEGER PRIMARY KEY, "
                    "is_shared BOOLEAN, "
                    "shared_at DATETIME, "
                    "topics JSON"
                    ")"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE document_topics ("
                    "document_id INTEGER, "
                    "topic VARCHAR(40), "
                    "PRIMARY KEY (document_id, topic)"
                    ")"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_documents_shared_catalog "
                    "ON documents (is_shared, shared_at, id)"
                )
            )

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            inspector = inspect(connection)
            topic_indexes = [
                index_data["name"]
                for index_data in inspector.get_indexes("document_topics")
            ]
            assert "ix_document_topics_topic_document" in topic_indexes

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()

            inspector.clear_cache()
            assert not inspector.has_table("document_topics")
    finally:
        engine.dispose()


def test_updating_document_topics_synchronizes_rows(tmp_path):
    migration = _load_migration()
    sqlite_file = tmp_path / "test_topics_sync.sqlite"
    engine = create_engine(f"sqlite:///{sqlite_file}")
    session = None
    try:
        tables_to_create = [
            table
            for table in Base.metadata.sorted_tables
            if table.name != "document_topics"
        ]
        Base.metadata.create_all(engine, tables=tables_to_create)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, is_admin) "
                    "VALUES (1, 'alice', 'hash', 0)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO documents ("
                    "id, user_id, title, format, file_path, file_size, "
                    "num_pages, processing_status"
                    ") VALUES (10, 1, 'Doc', 'pdf', '/tmp/doc.pdf', 10, 1, 'extracted')"
                )
            )

        session = Session(engine)
        document = session.get(Document, 10)
        assert document is not None

        document.topics = ["Artificial Intelligence"]
        with pytest.raises(OperationalError):
            session.commit()
        session.rollback()

        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

        document = session.get(Document, 10)
        assert document is not None

        document.topics = [" Machine Learning ", "AI", "ai", ""]
        session.commit()

        topic_rows = session.scalars(
            select(DocumentTopic).where(DocumentTopic.document_id == 10)
        ).all()
        synced_topics = sorted(row.topic for row in topic_rows)
        assert synced_topics == ["ai", "machine learning"]

        document.topics = ["Data Science"]
        session.commit()

        updated_rows = session.scalars(
            select(DocumentTopic).where(DocumentTopic.document_id == 10)
        ).all()
        assert [row.topic for row in updated_rows] == ["data science"]

        document.topics = []
        session.commit()

        cleared_rows = session.scalars(
            select(DocumentTopic).where(DocumentTopic.document_id == 10)
        ).all()
        assert cleared_rows == []
    finally:
        if session is not None:
            session.close()
        engine.dispose()


def test_document_topics_sync_with_active_session(db, test_user, test_document):
    test_document.topics = [" Distributed Systems ", "Storage", "storage"]
    db.commit()

    topic_rows = db.scalars(
        select(DocumentTopic).where(DocumentTopic.document_id == test_document.id)
    ).all()
    synced_topics = sorted(row.topic for row in topic_rows)
    assert synced_topics == ["distributed systems", "storage"]

    test_document.topics = ["Caching"]
    db.commit()

    updated_rows = db.scalars(
        select(DocumentTopic).where(DocumentTopic.document_id == test_document.id)
    ).all()
    assert [row.topic for row in updated_rows] == ["caching"]
