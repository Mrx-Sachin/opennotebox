"""Stress and concurrency tests for each endpoint.

Pushes each endpoint to its limits under concurrent load to find:
- Where response times degrade
- Where errors start appearing
- Where resource exhaustion occurs (DB pool, connections, etc.)

Runs hermetically against SQLite (no Docker needed) with mocked LLM backends.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models import Conversation, Document, ExtractedText, User
from app.services.auth_service import get_password_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fire_requests(
    client: AsyncClient,
    method: str,
    url: str,
    headers: Dict[str, str],
    json_body: Dict[str, Any] | None,
    n: int,
    concurrency: int,
) -> List[Tuple[int, float]]:
    """Fire n concurrent requests and return (status_code, latency_ms) tuples."""

    async def _one() -> Tuple[int, float]:
        t0 = time.monotonic()
        if json_body is not None:
            resp = await client.request(method, url, headers=headers, json=json_body)
        else:
            resp = await client.request(method, url, headers=headers)
        latency = (time.monotonic() - t0) * 1000
        return (resp.status_code, latency)

    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded() -> Tuple[int, float]:
        async with semaphore:
            return await _one()

    tasks = [_bounded() for _ in range(n)]
    return await asyncio.gather(*tasks)


def _analyze(results: List[Tuple[int, float]]) -> Dict[str, Any]:
    """Analyze request results and return a summary dict."""
    statuses: Dict[int, int] = defaultdict(int)
    latencies = []
    for status, latency in results:
        statuses[status] += 1
        latencies.append(latency)

    latencies.sort()
    n = len(latencies)
    return {
        "total": n,
        "statuses": dict(statuses),
        "success_count": sum(v for k, v in statuses.items() if 200 <= k < 400),
        "error_count": sum(v for k, v in statuses.items() if k >= 400),
        "latency_min_ms": round(latencies[0], 2) if n else 0,
        "latency_max_ms": round(latencies[-1], 2) if n else 0,
        "latency_mean_ms": round(statistics.mean(latencies), 2) if n else 0,
        "latency_p50_ms": round(latencies[int(n * 0.5)], 2) if n else 0,
        "latency_p95_ms": round(latencies[int(n * 0.95)], 2) if n else 0,
        "latency_p99_ms": round(latencies[min(int(n * 0.99), n - 1)], 2) if n else 0,
    }


# ---------------------------------------------------------------------------
# Stress test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def stress_user(test_engine) -> User:
    """Create a user specifically for stress tests.

    Uses SessionLocal directly so data persists across concurrent
    stress_client requests (not rolled back like the db fixture).
    """
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "stress_user").first()
        if existing:
            return existing
        user = User(
            username="stress_user",
            password_hash=get_password_hash("stresspass"),
            is_admin=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


@pytest.fixture()
def stress_token(stress_user) -> str:
    from datetime import datetime, timedelta, timezone
    from jose import jwt as jose_jwt

    _JWT_SECRET = "test_secret_32_chars_long_for_testing!"
    payload = {
        "sub": str(stress_user.id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "type": "access",
    }
    return jose_jwt.encode(payload, _JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def stress_auth_header(stress_token) -> dict:
    return {"Authorization": f"Bearer {stress_token}"}


@pytest.fixture()
def stress_conversation(stress_user) -> Conversation:
    """Create a conversation for stress testing chat endpoints."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        existing = db.query(Conversation).filter(
            Conversation.user_id == stress_user.id
        ).first()
        if existing:
            return existing
        conv = Conversation(
            user_id=stress_user.id,
            title="Stress Test Chat",
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return conv
    finally:
        db.close()


@pytest.fixture()
def stress_documents(stress_user, tmp_path) -> List[Document]:
    """Create 10 documents for stress testing."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        existing = db.query(Document).filter(
            Document.user_id == stress_user.id
        ).all()
        if existing:
            return existing
        docs = []
        for i in range(10):
            file_path = tmp_path / f"doc_{i}.pdf"
            file_path.write_bytes(b"%PDF- fake content")
            doc = Document(
                user_id=stress_user.id,
                title=f"Stress Doc {i}.pdf",
                format="pdf",
                file_path=str(file_path),
                file_size=16,
                num_pages=5,
                processing_status="extracted",
            )
            db.add(doc)
            db.flush()
            for page_num in range(1, 6):
                page = ExtractedText(
                    document_id=doc.id,
                    page_number=page_num,
                    content=f"Content of doc {i} page {page_num}. "
                            "This is test content for stress testing purposes.",
                )
                db.add(page)
            docs.append(doc)
        db.commit()
        return [db.refresh(d) or d for d in docs]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestAuthStress:
    """Stress test auth endpoints under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_logins(self, stress_client, stress_auth_header):
        """100 concurrent logins: the brute-force limiter (10/min per
        ip+username, added with the production-readiness audit) should absorb
        the burst with clean 429s - NOT 5xx, and NOT all 200s.

        The old version of this test asserted every attempt succeeds; that
        contract was the vulnerability. Successes are bounded rather than
        pinned to exactly 10 because earlier tests in the suite share the
        per-IP spray bucket within the 60s window."""
        results = await _fire_requests(
            client=stress_client,
            method="POST",
            url="/api/auth/login",
            headers={},
            json_body={"username": "stress_user", "password": "stresspass"},
            n=100,
            concurrency=20,
        )
        analysis = _analyze(results)
        statuses = analysis["statuses"]
        assert set(statuses) <= {200, 429}, (
            f"Unexpected statuses under load: {statuses}"
        )
        assert 429 in statuses, "Rate limiter did not engage under a 100x burst"
        assert statuses.get(200, 0) <= 10, (
            f"More than the per-window login allowance succeeded: {statuses}"
        )
        assert analysis["latency_p95_ms"] < 5000, (
            f"P95 login latency too high: {analysis['latency_p95_ms']}ms"
        )

    @pytest.mark.asyncio
    async def test_concurrent_me(self, stress_client, stress_auth_header):
        """200 concurrent /me requests should all succeed."""
        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url="/api/auth/me",
            headers=stress_auth_header,
            json_body=None,
            n=200,
            concurrency=50,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0
        assert analysis["latency_p95_ms"] < 2000

    @pytest.mark.asyncio
    async def test_concurrent_refresh(self, stress_client, stress_auth_header):
        """50 concurrent token refreshes should all succeed."""
        # Login first to get a refresh token for stress_user
        resp = await stress_client.post(
            "/api/auth/login",
            json={"username": "stress_user", "password": "stresspass"},
        )
        refresh_val = resp.json()["refresh_token"]

        results = await _fire_requests(
            client=stress_client,
            method="POST",
            url="/api/auth/refresh",
            headers={},
            json_body={"refresh_token": refresh_val},
            n=50,
            concurrency=20,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0


class TestDocumentStress:
    """Stress test document endpoints under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_list_documents(
        self, stress_client, stress_auth_header, stress_documents
    ):
        """100 concurrent list requests should all succeed."""
        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url="/api/documents",
            headers=stress_auth_header,
            json_body=None,
            n=100,
            concurrency=25,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0
        assert analysis["latency_p95_ms"] < 5000

    @pytest.mark.asyncio
    async def test_concurrent_get_document(
        self, stress_client, stress_auth_header, stress_documents
    ):
        """50 concurrent get requests for different documents."""
        doc_id = stress_documents[0].id

        async def _get_doc() -> Tuple[int, float]:
            t0 = time.monotonic()
            resp = await stress_client.get(
                f"/api/documents/{doc_id}", headers=stress_auth_header
            )
            latency = (time.monotonic() - t0) * 1000
            return (resp.status_code, latency)

        tasks = [_get_doc() for _ in range(50)]
        results = await asyncio.gather(*tasks)
        analysis = _analyze(results)
        assert analysis["error_count"] == 0

    @pytest.mark.asyncio
    async def test_concurrent_update_progress(
        self, stress_client, stress_auth_header, stress_documents
    ):
        """50 concurrent progress updates - SQLite can't handle full concurrency."""
        doc_id = stress_documents[0].id

        results = await _fire_requests(
            client=stress_client,
            method="PUT",
            url=f"/api/documents/{doc_id}/progress",
            headers=stress_auth_header,
            json_body={"page": 3, "position": 0.75},
            n=50,
            concurrency=10,
        )
        analysis = _analyze(results)
        # SQLite has single-writer limitation; accept majority success
        assert analysis["success_count"] >= analysis["total"] * 0.5

    @pytest.mark.asyncio
    async def test_concurrent_get_pages(
        self, stress_client, stress_auth_header, stress_documents
    ):
        """100 concurrent page retrieval requests."""
        doc_id = stress_documents[0].id

        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url=f"/api/documents/{doc_id}/pages",
            headers=stress_auth_header,
            json_body=None,
            n=100,
            concurrency=25,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0


class TestChatStress:
    """Stress test chat endpoints under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_create_conversation(
        self, stress_client, stress_auth_header
    ):
        """30 concurrent conversation creations should all succeed."""
        results = await _fire_requests(
            client=stress_client,
            method="POST",
            url="/api/chat/sessions",
            headers=stress_auth_header,
            json_body=None,
            n=30,
            concurrency=10,
        )
        analysis = _analyze(results)
        # SQLite concurrency is limited; accept up to 50% success under stress
        assert analysis["success_count"] >= analysis["total"] * 0.5

    @pytest.mark.asyncio
    async def test_concurrent_list_conversations(
        self, stress_client, stress_auth_header
    ):
        """100 concurrent list conversations requests."""
        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url="/api/chat/sessions",
            headers=stress_auth_header,
            json_body=None,
            n=100,
            concurrency=25,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0

    @pytest.mark.asyncio
    async def test_concurrent_get_messages(
        self, stress_client, stress_auth_header, stress_conversation
    ):
        """50 concurrent message retrieval requests."""
        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url=f"/api/chat/sessions/{stress_conversation.id}/messages",
            headers=stress_auth_header,
            json_body=None,
            n=50,
            concurrency=15,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0

    @pytest.mark.asyncio
    @patch(
        "app.services.chat_engine.ChatEngine.answer_general",
        new_callable=AsyncMock,
    )
    async def test_concurrent_chat_messages(
        self, mock_answer, stress_client, stress_auth_header, stress_conversation
    ):
        """20 concurrent chat messages with mocked LLM response."""
        async def mock_resp(**kwargs):
            await asyncio.sleep(0.01)  # Simulate minimal LLM latency
            return {
                "content": "Stress test response.",
                "citations": [],
                "token_count": 5,
            }

        mock_answer.side_effect = mock_resp

        results = await _fire_requests(
            client=stress_client,
            method="POST",
            url=f"/api/chat/sessions/{stress_conversation.id}/messages",
            headers=stress_auth_header,
            json_body={"content": "Stress test message"},
            n=20,
            concurrency=5,
        )
        analysis = _analyze(results)
        # SQLite concurrency is limited; accept majority success under stress
        assert analysis["success_count"] >= analysis["total"] * 0.5, (
            f"Chat messages failed under load: {analysis['statuses']}"
        )


class TestMetricsStress:
    """Stress test metrics endpoints under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_metrics_snapshot(
        self, stress_client, stress_auth_header
    ):
        """200 concurrent metrics requests should all succeed."""
        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url="/api/metrics",
            headers=stress_auth_header,
            json_body=None,
            n=200,
            concurrency=50,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0
        # Metrics endpoint is read-only and should be very fast
        assert analysis["latency_p95_ms"] < 2000

    @pytest.mark.asyncio
    async def test_concurrent_metrics_summary(
        self, stress_client, stress_auth_header
    ):
        """200 concurrent metrics summary requests."""
        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url="/api/metrics/summary",
            headers=stress_auth_header,
            json_body=None,
            n=200,
            concurrency=50,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0


class TestHealthEndpointStress:
    """Stress test the health endpoint (no auth required)."""

    @pytest.mark.asyncio
    async def test_concurrent_health(self, stress_client):
        """1000 concurrent health checks should all succeed instantly."""
        results = await _fire_requests(
            client=stress_client,
            method="GET",
            url="/health",
            headers={},
            json_body=None,
            n=1000,
            concurrency=100,
        )
        analysis = _analyze(results)
        assert analysis["error_count"] == 0
        assert analysis["latency_p99_ms"] < 1500, (
            f"Health endpoint too slow under load: {analysis['latency_p99_ms']}ms"
        )


class TestMixedWorkload:
    """Mixed workload stress test: simulate a real user session."""

    @pytest.mark.asyncio
    @patch(
        "app.services.chat_engine.ChatEngine.answer_general",
        new_callable=AsyncMock,
    )
    async def test_mixed_concurrent_workload(
        self, mock_answer, stress_client, stress_auth_header,
        stress_conversation, stress_documents
    ):
        """Simulate 20 concurrent users doing mixed operations."""
        mock_answer.return_value = {
            "content": "Mixed workload test response.",
            "citations": [],
            "token_count": 5,
        }

        doc_id = stress_documents[0].id
        conv_id = stress_conversation.id
        errors = []

        async def _user_session(user_id: int) -> None:
            """Simulate a single user session's worth of requests."""
            tasks = []

            # List documents
            tasks.append(stress_client.get("/api/documents", headers=stress_auth_header))
            # Get a document
            tasks.append(stress_client.get(f"/api/documents/{doc_id}", headers=stress_auth_header))
            # Get pages
            tasks.append(stress_client.get(f"/api/documents/{doc_id}/pages", headers=stress_auth_header))
            # Update progress
            tasks.append(stress_client.put(
                f"/api/documents/{doc_id}/progress",
                headers=stress_auth_header,
                json={"page": 2, "position": 0.5},
            ))
            # List conversations
            tasks.append(stress_client.get("/api/chat/sessions", headers=stress_auth_header))
            # Get messages
            tasks.append(stress_client.get(f"/api/chat/sessions/{conv_id}/messages", headers=stress_auth_header))
            # Send a chat message
            tasks.append(stress_client.post(
                f"/api/chat/sessions/{conv_id}/messages",
                headers=stress_auth_header,
                json={"content": f"Mixed workload message {user_id}"},
            ))
            # Get /me
            tasks.append(stress_client.get("/api/auth/me", headers=stress_auth_header))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    errors.append(f"User {user_id} task {i}: {r}")
                elif hasattr(r, "status_code") and r.status_code >= 400:
                    errors.append(
                        f"User {user_id} task {i}: HTTP {r.status_code}"
                    )

        # Run 20 concurrent user sessions
        sessions = [_user_session(i) for i in range(20)]
        await asyncio.gather(*sessions)

        # SQLite's single-writer limitation means concurrent writes will
        # cause "database is locked" errors. We verify that the majority
        # of requests still succeed, rather than requiring 100% success
        # (which PostgreSQL would achieve).
        total_tasks = 20 * 9  # 20 users x 9 tasks each
        error_rate = len(errors) / total_tasks
        assert error_rate < 0.5, (
            f"Mixed workload error rate too high: {error_rate:.0%} ({len(errors)}/{total_tasks}). "
            f"Sample errors: {errors[:3]}"
        )

# NOTE: Stress tests run against SQLite which has limited concurrency.
# High concurrency levels (50+) will cause "database is locked" errors
# under SQLite's single-writer limitation. Tests account for this by
# using moderate concurrency levels and accepting partial success where
# appropriate. Production deployments use PostgreSQL which handles
# concurrent access properly.
