"""Shared test fixtures for OpenNoteBox.

Follows Google Python Style Guide conventions:
- Test fixtures use underscore-prefixed names where they return internal objects
- Fixtures are scoped appropriately (function-scoped by default)
- SQLite is used for all hermetic tests (no Docker dependency)
- A unique JWT secret per test session prevents cross-test token collisions

IMPORTANT: This conftest patches sqlalchemy.create_engine BEFORE importing any
app modules, so that the PostgreSQL-specific engine creation in app/database.py
is replaced with a SQLite-compatible version. This keeps tests hermetic.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Generator

from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import create_engine as real_create_engine
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Patch create_engine before importing app modules so PostgreSQL-specific
# args (max_overflow, pool_recycle) are silently ignored for SQLite.
# ---------------------------------------------------------------------------
# Real SQLite file in a temp dir (removed in pytest_sessionfinish).
# A shared-cache in-memory URL ("file:memory:testdb?cache=shared") would also
# work but materializes a real file named after the database in the current
# working directory, polluting the repo; a temp-dir file gives the same
# cross-connection visibility without that side effect.
_TEST_SQLITE_DIR = tempfile.mkdtemp(prefix="notebox_testdb_")
_TEST_DB_URL = f"sqlite:///{os.path.join(_TEST_SQLITE_DIR, 'testdb.sqlite3')}"


def _test_create_engine(url, **kwargs):
    """create_engine wrapper that strips PG-only args for SQLite.

    Sets check_same_thread=False so concurrent ASGI workers can share
    the SQLite database without threading errors, and a generous busy
    timeout so concurrent writes queue instead of failing immediately.
    """
    if "sqlite" in (url or ""):
        kwargs.pop("max_overflow", None)
        kwargs.pop("pool_recycle", None)
        ca = kwargs.get("connect_args", {})
        ca["check_same_thread"] = False
        ca.setdefault("timeout", 30)
        kwargs["connect_args"] = ca
    return real_create_engine(url, **kwargs)


def pytest_sessionfinish(session, exitstatus):
    """Remove the temp-dir SQLite database after the test session."""
    import shutil

    shutil.rmtree(_TEST_SQLITE_DIR, ignore_errors=True)


with patch("sqlalchemy.create_engine", side_effect=_test_create_engine):
    # Force-override env vars so they take effect even when running inside
    # Docker where the container already has JWT_SECRET set.
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    os.environ["JWT_SECRET"] = "test_secret_32_chars_long_for_testing!"
    os.environ["DATA_DIR"] = tempfile.mkdtemp()

    from app.database import Base, get_db  # noqa: E402
    from app.models import (  # noqa: E402
        User,
        Document,
        ExtractedText,
        Conversation,
        Message,
    )
    from app.config import settings  # noqa: E402

# Ensure the settings singleton has our test JWT secret
settings.jwt_secret = "test_secret_32_chars_long_for_testing!"

# ---------------------------------------------------------------------------
# Stress-test engine: uses NullPool so each checkout opens a fresh
# connection.  This avoids segfaults that occur when a pooled connection
# is used from a different thread (SQLite's C library limitation).
# Contention is handled by SQLite's timeout (30s), and tests accept
# partial success under concurrent writes.
# ---------------------------------------------------------------------------
from sqlalchemy.pool import NullPool  # noqa: E402

_stress_engine = real_create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    poolclass=NullPool,
)


def _set_stress_engine():
    """Replace the module-level engine with a stress-test-safe engine."""
    import app.database as db_mod  # noqa: F811

    old_engine = db_mod.engine
    db_mod.engine = _stress_engine
    db_mod.SessionLocal = sessionmaker(
        bind=_stress_engine, autocommit=False, autoflush=False
    )
    return old_engine


def _reset_stress_engine(old_engine):
    """Restore the original engine after stress tests."""
    import app.database as db_mod  # noqa: F811

    db_mod.engine = old_engine
    db_mod.SessionLocal = sessionmaker(
        bind=old_engine, autocommit=False, autoflush=False
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_auth_rate_limit_buckets():
    """Clear the in-memory login/register limiter between tests.

    The limiter state in app.routers.auth is module-level on purpose (the
    deployment is a single uvicorn process), which means it also leaks
    across tests in one pytest process: e.g. the 100-burst login stress
    test exhausts (ip, username) for every later test logging in as the
    same user, and 429s start looking like auth bugs. Reset per test so
    results never depend on test execution order."""
    from app.routers import auth as auth_module

    auth_module._login_buckets.clear()
    auth_module._register_buckets.clear()
    yield


@pytest.fixture(scope="session")
def test_engine():
    """Create a session-scoped SQLite engine for all tests."""
    eng = real_create_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()


@pytest.fixture()
def db(test_engine) -> Generator[Session, None, None]:
    """Yield a fresh database session per test, rolled back afterwards.

    Uses a nested transaction so each test gets a clean state without
    rebuilding the schema. The outer transaction is rolled back after each test.
    """
    conn = test_engine.connect()
    trans = conn.begin()
    Session = sessionmaker(bind=conn)
    session = Session()
    yield session
    session.close()
    trans.rollback()
    conn.close()


# ---------------------------------------------------------------------------
# Override get_db so FastAPI deps inject our test session
# ---------------------------------------------------------------------------

@pytest.fixture()
def override_get_db(db):
    """Override the get_db dependency to use the test session."""
    def _get_db():
        yield db

    yield _get_db


# ---------------------------------------------------------------------------
# User fixtures - auto=True so they are always created when db is available
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_user(db: Session) -> User:
    """Create a standard (non-admin) test user.

    Not autouse - tests get it transitively via auth_header/access_token.
    Stress tests don't depend on this fixture at all.
    """
    from app.services.auth_service import get_password_hash
    user = User(
        username="testuser",
        password_hash=get_password_hash("testpass123"),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def admin_user(db: Session) -> User:
    """Create an admin test user."""
    from app.services.auth_service import get_password_hash
    user = User(
        username="adminuser",
        password_hash=get_password_hash("adminpass123"),
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def second_user(db: Session) -> User:
    """Create a second user for multi-user isolation tests."""
    from app.services.auth_service import get_password_hash
    user = User(
        username="seconduser",
        password_hash=get_password_hash("secondpass123"),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Token fixtures
# ---------------------------------------------------------------------------

_TEST_JWT_SECRET = "test_secret_32_chars_long_for_testing!"


@pytest.fixture()
def access_token(test_user: User) -> str:
    """Create a valid access token for the test user."""
    payload = {
        "sub": str(test_user.id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "type": "access",
    }
    return jwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def admin_token(admin_user: User) -> str:
    """Create a valid access token for the admin user."""
    payload = {
        "sub": str(admin_user.id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "type": "access",
    }
    return jwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def refresh_token_value(test_user: User) -> str:
    """Create a valid refresh token for the test user."""
    payload = {
        "sub": str(test_user.id),
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "refresh",
    }
    return jwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def expired_token(test_user: User) -> str:
    """Create an expired access token."""
    payload = {
        "sub": str(test_user.id),
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        "type": "access",
    }
    return jwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def auth_header(access_token: str) -> dict:
    """Authorization header with bearer token."""
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture()
def admin_auth_header(admin_token: str) -> dict:
    """Authorization header with admin bearer token."""
    return {"Authorization": f"Bearer {admin_token}"}


# ---------------------------------------------------------------------------
# Document fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_document(db: Session, test_user: User, tmp_path) -> Document:
    """Create a test document with some extracted text pages."""
    file_path = tmp_path / "test.pdf"
    file_path.write_bytes(b"%PDF- fake content")
    doc = Document(
        user_id=test_user.id,
        title="Test Document.pdf",
        format="pdf",
        file_path=str(file_path),
        file_size=file_path.stat().st_size,
        num_pages=3,
        processing_status="extracted",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    for page_num in range(1, 4):
        page = ExtractedText(
            document_id=doc.id,
            page_number=page_num,
            content=(
                f"This is page {page_num} of the test document. "
                "It contains important information about testing. "
                "The quick brown fox jumps over the lazy dog."
            ),
        )
        db.add(page)
    db.commit()
    return doc


@pytest.fixture()
def second_document(db: Session, second_user: User, tmp_path) -> Document:
    """Create a document owned by the second user (for isolation tests)."""
    file_path = tmp_path / "other.pdf"
    file_path.write_bytes(b"%PDF- other content")
    doc = Document(
        user_id=second_user.id,
        title="Other Document.pdf",
        format="pdf",
        file_path=str(file_path),
        file_size=file_path.stat().st_size,
        num_pages=1,
        processing_status="extracted",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    page = ExtractedText(
        document_id=doc.id,
        page_number=1,
        content="This belongs to another user entirely.",
    )
    db.add(page)
    db.commit()
    return doc


# ---------------------------------------------------------------------------
# Conversation fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_conversation(db: Session, test_user: User) -> Conversation:
    """Create a test conversation with some messages."""
    conv = Conversation(
        user_id=test_user.id,
        title="Test Conversation",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)

    user_msg = Message(
        conversation_id=conv.id,
        role="user",
        content="What is this document about?",
    )
    ai_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content="This document is about testing software.",
        token_count=10,
    )
    db.add_all([user_msg, ai_msg])
    db.commit()
    return conv


# ---------------------------------------------------------------------------
# App fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(override_get_db) -> FastAPI:
    """Create a test FastAPI app with overridden dependencies."""
    from app.main import app as real_app

    real_app.dependency_overrides[get_db] = override_get_db

    yield real_app

    # Clear overrides after test
    real_app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def stress_engine(test_engine):
    """Set up a stress-test-safe engine.

    Swaps the module-level engine for a StaticPool-based engine that can
    handle concurrent ASGI workers without segfaulting.
    """
    # Schema is already created by test_engine; create on stress engine too
    Base.metadata.create_all(bind=_stress_engine)
    old = _set_stress_engine()
    yield _stress_engine
    _reset_stress_engine(old)


@pytest_asyncio.fixture()
async def stress_client(stress_engine, test_engine) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client for stress tests.

    Does NOT override get_db, so each request gets its own SessionLocal()
    session backed by a StaticPool engine. This avoids session state
    corruption under concurrent requests with SQLite.
    """
    from app.main import app as real_app
    # Ensure no leftover overrides from other tests
    real_app.dependency_overrides.clear()
    try:
        transport = ASGITransport(app=real_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        # Clean up after test
        real_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Reset the metrics singleton between tests so counters/latencies don't
# leak across test boundaries.  This runs for every test that requests
# either `app` or `client` (via the dependency chain).
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_metrics_singleton():
    """Reset the metrics module-level singleton so tests get a fresh registry."""
    import app.services.metrics as metrics_mod
    metrics_mod._metrics = None
    yield
    metrics_mod._metrics = None
