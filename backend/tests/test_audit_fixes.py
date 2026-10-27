"""Regression tests for the audit fixes:

- SSRF guard on URL uploads (_validate_download_url)
- admin-only writes to the shared LLM backend registry
- first-registered user gets the admin role (bootstrap)
- chunk lifecycle cleanup (delete_document_chunks)
- hybrid retrieval running off the event loop (asyncio.to_thread)
"""
from __future__ import annotations

import asyncio
import socket
import threading

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import create_engine as real_create_engine
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from app.routers import documents
from app.services import hybrid_rag, retrieval


def _fake_addrinfo(ip: str):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return fake


def _fake_gaierror(host, port, *args, **kwargs):
    raise socket.gaierror("no such host")


class TestValidateDownloadUrl:
    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(HTTPException) as exc:
            documents._validate_download_url("ftp://example.com/a.pdf")
        assert exc.value.status_code == 400

    def test_rejects_file_scheme(self) -> None:
        with pytest.raises(HTTPException) as exc:
            documents._validate_download_url("file:///etc/passwd")
        assert exc.value.status_code == 400

    def test_rejects_missing_host(self) -> None:
        with pytest.raises(HTTPException) as exc:
            documents._validate_download_url("https:///file.pdf")
        assert exc.value.status_code == 400

    def test_rejects_unresolvable_host(self, monkeypatch) -> None:
        monkeypatch.setattr(documents.socket, "getaddrinfo", _fake_gaierror)
        with pytest.raises(HTTPException) as exc:
            documents._validate_download_url("https://nowhere.invalid/a.pdf")
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "ip", ["127.0.0.1", "127.9.9.9", "10.0.0.5", "192.168.1.1", "172.16.0.9"]
    )
    def test_blocks_loopback_and_private(self, monkeypatch, ip: str) -> None:
        monkeypatch.setattr(documents.socket, "getaddrinfo", _fake_addrinfo(ip))
        with pytest.raises(HTTPException) as exc:
            documents._validate_download_url("https://internal.example/a.pdf")
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "ip", ["169.254.169.254", "0.0.0.0", "224.0.0.1", "240.0.0.1"]
    )
    def test_reserved_ranges_stay_blocked_even_with_override(
        self, monkeypatch, ip: str
    ) -> None:
        # DOWNLOAD_ALLOW_PRIVATE_HOSTS must not become an open door to the
        # cloud metadata endpoint or other special-purpose ranges.
        monkeypatch.setattr(documents.settings, "download_allow_private_hosts", True)
        monkeypatch.setattr(documents.socket, "getaddrinfo", _fake_addrinfo(ip))
        with pytest.raises(HTTPException) as exc:
            documents._validate_download_url("https://metadata.example/a.pdf")
        assert exc.value.status_code == 400

    def test_allows_public_address(self, monkeypatch) -> None:
        monkeypatch.setattr(
            documents.socket, "getaddrinfo", _fake_addrinfo("93.184.216.34")
        )
        documents._validate_download_url("https://example.com/a.pdf")

    def test_allow_private_hosts_override_opens_loopback(self, monkeypatch) -> None:
        monkeypatch.setattr(documents.settings, "download_allow_private_hosts", True)
        monkeypatch.setattr(documents.socket, "getaddrinfo", _fake_addrinfo("127.0.0.1"))
        documents._validate_download_url("http://localhost:9000/a.pdf")


class TestSettingsRegistryRbac:
    _EVIL = {
        "llm": {
            "backends": [
                {
                    "name": "evil",
                    "base_url": "http://evil.example/v1",
                    "model": "m",
                    "api_key": None,
                }
            ]
        }
    }

    async def test_non_admin_backend_write_forbidden(
        self, client: AsyncClient, auth_header: dict
    ) -> None:
        res = await client.put("/api/settings", headers=auth_header, json=self._EVIL)
        assert res.status_code == 403

    async def test_admin_backend_write_allowed(
        self, client: AsyncClient, admin_auth_header: dict
    ) -> None:
        res = await client.put(
            "/api/settings", headers=admin_auth_header, json=self._EVIL
        )
        assert res.status_code == 200

    async def test_non_admin_per_user_active_still_allowed(
        self, client: AsyncClient, auth_header: dict
    ) -> None:
        # The gate must only cover the shared registry, not per-user prefs.
        res = await client.put(
            "/api/settings", headers=auth_header, json={"llm": {"active": "gpt-4o"}}
        )
        assert res.status_code == 200


class TestFirstUserBootstrap:
    @pytest.fixture(autouse=True)
    def _fresh_register_buckets(self):
        # The register endpoint rate-limits per IP; other tests in the suite
        # already burn part of this process's budget, so reset it here.
        from app.routers import auth
        auth._register_buckets.clear()
        yield
        auth._register_buckets.clear()

    def test_first_registered_user_is_admin_second_is_not(
        self, tmp_path, monkeypatch
    ) -> None:
        from app.config import settings
        from app.database import Base
        from app.routers.auth import register
        from app.schemas import UserCreate

        monkeypatch.setattr(settings, "allow_registration", True)
        engine = real_create_engine(f"sqlite:///{tmp_path}/bootstrap.db")
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine)()

        class _StubClient:
            host = "127.0.0.1"

        class _StubRequest:
            client = _StubClient()

        request = _StubRequest()
        try:
            first = asyncio.run(
                register(request, UserCreate(username="first", password="secret123"), db=db)
            )
            assert first.is_admin is True
            second = asyncio.run(
                register(request, UserCreate(username="second", password="secret123"), db=db)
            )
            assert second.is_admin is False
        finally:
            db.close()
            engine.dispose()


class TestChunkLifecycle:
    @pytest.fixture()
    def chunk_db(self, tmp_path):
        engine = real_create_engine(f"sqlite:///{tmp_path}/chunks.db")
        with engine.connect() as conn:
            conn.execute(
                sql_text(
                    "CREATE TABLE chunk ("
                    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " document_id INTEGER NOT NULL,"
                    " page_number INTEGER NOT NULL,"
                    " chunk_index INTEGER NOT NULL,"
                    " text TEXT NOT NULL,"
                    " emb BLOB)"
                )
            )
            conn.commit()
        db = sessionmaker(bind=engine)()
        yield db
        db.close()
        engine.dispose()

    def test_missing_chunk_table_returns_zero(self, tmp_path) -> None:
        engine = real_create_engine(f"sqlite:///{tmp_path}/empty.db")
        db = sessionmaker(bind=engine)()
        try:
            assert retrieval.delete_document_chunks(db, 1) == 0
        finally:
            db.close()
            engine.dispose()

    def test_removes_only_the_targets_chunks(self, chunk_db: Session) -> None:
        for doc in (1, 2):
            for i in range(3):
                chunk_db.execute(
                    sql_text(
                        "INSERT INTO chunk (document_id, page_number, chunk_index, text)"
                        " VALUES (:d, 1, :i, :t)"
                    ),
                    {"d": doc, "i": i, "t": f"doc{doc} chunk {i}"},
                )
        chunk_db.commit()

        removed = retrieval.delete_document_chunks(chunk_db, 1)
        chunk_db.commit()

        assert removed == 3
        left = chunk_db.execute(
            sql_text("SELECT document_id, COUNT(*) FROM chunk GROUP BY document_id")
        ).fetchall()
        assert [(d, c) for d, c in left] == [(2, 3)]


class TestHybridRunsOffEventLoop:
    async def test_answer_hybrid_offloads_retrieve_hybrid_to_thread(
        self, monkeypatch
    ) -> None:
        from unittest.mock import MagicMock

        calls: dict = {}

        def fake_retrieve(*args, **kwargs):
            calls["thread"] = threading.current_thread()
            return {"combined_sources": [], "vector_sources": [], "graph_subgraph": {}}

        monkeypatch.setattr(hybrid_rag, "retrieve_hybrid", fake_retrieve)
        monkeypatch.setattr(hybrid_rag, "SessionLocal", MagicMock)
        monkeypatch.setattr(hybrid_rag, "_kg_built_for_docs", lambda db, ids: False)
        monkeypatch.setattr(
            hybrid_rag.chat_engine, "should_skip_rewrite", lambda q, h=None: True
        )

        result = await hybrid_rag.answer_hybrid("what is a jitt?", [1], mode="hybrid")

        assert "thread" in calls, "retrieve_hybrid was never called"
        assert calls["thread"] is not threading.current_thread(), (
            "retrieve_hybrid ran on the event loop thread; it must be offloaded"
        )
        assert result["citations"] == []
