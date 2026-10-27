"""Tests for pure-ASGI pre-body upload guard middleware."""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from jose import jwt
import pytest
from starlette.datastructures import Headers

from app.config import settings
from app.main import app
from app.models import Document
from app.services.auth_service import create_access_token, create_refresh_token
from app.services.upload_guard import UploadGuardMiddleware

SAMPLE_PDF_BYTES: bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


async def invoke_asgi_request(
    target_app: Any,
    method: str,
    path: str,
    request_headers: list[tuple[bytes, bytes]],
    body_chunks: list[bytes] | None = None,
) -> tuple[int, list[tuple[bytes, bytes]], bytes, int, int]:
    """Execute an ASGI HTTP request with tracking receive and return response details."""
    payload_chunks = body_chunks or []
    chunk_index = 0
    receive_call_count = 0
    total_bytes_consumed = 0

    async def tracking_receive() -> dict[str, Any]:
        nonlocal chunk_index, receive_call_count, total_bytes_consumed
        receive_call_count += 1
        if chunk_index < len(payload_chunks):
            current_chunk = payload_chunks[chunk_index]
            chunk_index += 1
            total_bytes_consumed += len(current_chunk)
            has_more = chunk_index < len(payload_chunks)
            return {"type": "http.request", "body": current_chunk, "more_body": has_more}
        return {"type": "http.request", "body": b"", "more_body": False}

    response_status = 500
    response_headers: list[tuple[bytes, bytes]] = []
    response_body_parts: list[bytes] = []

    async def tracking_send(message: dict[str, Any]) -> None:
        nonlocal response_status, response_headers, response_body_parts
        if message["type"] == "http.response.start":
            response_status = message["status"]
            response_headers = list(message.get("headers", []))
        elif message["type"] == "http.response.body":
            response_body_parts.append(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": request_headers,
    }

    await target_app(scope, tracking_receive, tracking_send)
    return (
        response_status,
        response_headers,
        b"".join(response_body_parts),
        receive_call_count,
        total_bytes_consumed,
    )


def build_multipart_body(boundary: str, filename: str, file_bytes: bytes) -> list[bytes]:
    """Construct multipart form payload chunks."""
    part_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    part_footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return [part_header, file_bytes, part_footer]


@pytest.mark.asyncio
async def test_unauthenticated_upload_never_consumes_receive():
    """Verify missing auth header is rejected with 401 without consuming request body."""
    boundary = "----WebKitFormBoundaryProofZero"
    chunks = build_multipart_body(boundary, "test.pdf", b"X" * 1024 * 1024)
    total_length = sum(len(chunk) for chunk in chunks)
    request_headers = [
        (b"host", b"testserver"),
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
        (b"content-length", str(total_length).encode("ascii")),
    ]

    (
        status_code,
        response_headers,
        response_body,
        receive_count,
        bytes_consumed,
    ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)

    assert status_code == 401
    assert receive_count == 0
    assert bytes_consumed == 0
    assert b"Missing or invalid token" in response_body
    header_map = Headers(raw=response_headers)
    assert header_map.get("content-type") == "application/json"


@pytest.mark.asyncio
async def test_malformed_auth_headers_never_consume_receive():
    """Verify invalid header formats reject with 401 and zero receive consumption."""
    boundary = "----WebKitFormBoundaryMalformed"
    chunks = build_multipart_body(boundary, "test.pdf", b"X" * 65536)
    total_length = sum(len(chunk) for chunk in chunks)

    invalid_authorizations = [
        b"Basic dXNlcjpwYXNz",
        b"Bearer",
        b"Bearer ",
        b"Token something",
        b"CustomScheme xyz",
    ]

    for auth_value in invalid_authorizations:
        request_headers = [
            (b"host", b"testserver"),
            (b"authorization", auth_value),
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
            (b"content-length", str(total_length).encode("ascii")),
        ]
        (
            status_code,
            unused_headers,
            response_body,
            receive_count,
            bytes_consumed,
        ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)
        assert status_code == 401
        assert receive_count == 0
        assert bytes_consumed == 0
        assert b"Missing or invalid token" in response_body


@pytest.mark.asyncio
async def test_invalid_signature_and_forged_tokens_never_consume_receive():
    """Verify forged or garbage tokens reject with 401 and zero receive consumption."""
    boundary = "----WebKitFormBoundaryForged"
    chunks = build_multipart_body(boundary, "test.pdf", b"X" * 65536)
    total_length = sum(len(chunk) for chunk in chunks)

    forged_tokens = [
        "not.a.real.jwt.token",
        jwt.encode({"sub": "1", "type": "access"}, "wrong-secret-key", algorithm="HS256"),
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalidpayload.invalidsig",
    ]

    for forged_token in forged_tokens:
        request_headers = [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {forged_token}".encode("ascii")),
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
            (b"content-length", str(total_length).encode("ascii")),
        ]
        (
            status_code,
            unused_headers,
            response_body,
            receive_count,
            bytes_consumed,
        ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)
        assert status_code == 401
        assert receive_count == 0
        assert bytes_consumed == 0
        assert b"Invalid or expired token" in response_body


@pytest.mark.asyncio
async def test_expired_token_never_consumes_receive(test_user):
    """Verify expired access token rejects with 401 and zero receive consumption."""
    boundary = "----WebKitFormBoundaryExpired"
    chunks = build_multipart_body(boundary, "test.pdf", b"X" * 65536)
    total_length = sum(len(chunk) for chunk in chunks)

    expired_payload = {
        "sub": str(test_user.id),
        "exp": datetime.now(timezone.utc) - timedelta(hours=2),
        "type": "access",
    }
    expired_token_string = jwt.encode(expired_payload, settings.jwt_secret, algorithm="HS256")

    request_headers = [
        (b"host", b"testserver"),
        (b"authorization", f"Bearer {expired_token_string}".encode("ascii")),
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
        (b"content-length", str(total_length).encode("ascii")),
    ]

    (
        status_code,
        unused_headers,
        response_body,
        receive_count,
        bytes_consumed,
    ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)

    assert status_code == 401
    assert receive_count == 0
    assert bytes_consumed == 0
    assert b"Invalid or expired token" in response_body


@pytest.mark.asyncio
async def test_refresh_token_as_bearer_never_consumes_receive(test_user):
    """Verify refresh token presented as Bearer token is rejected before body consumption."""
    boundary = "----WebKitFormBoundaryRefresh"
    chunks = build_multipart_body(boundary, "test.pdf", b"X" * 65536)
    total_length = sum(len(chunk) for chunk in chunks)

    refresh_token_string = create_refresh_token({"sub": str(test_user.id)})
    request_headers = [
        (b"host", b"testserver"),
        (b"authorization", f"Bearer {refresh_token_string}".encode("ascii")),
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
        (b"content-length", str(total_length).encode("ascii")),
    ]

    (
        status_code,
        unused_headers,
        response_body,
        receive_count,
        bytes_consumed,
    ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)

    assert status_code == 401
    assert receive_count == 0
    assert bytes_consumed == 0
    assert b"Invalid or expired token" in response_body


@pytest.mark.asyncio
async def test_missing_or_invalid_subject_claims_never_consume_receive():
    """Verify tokens with missing or non-integer sub claim reject with 401."""
    boundary = "----WebKitFormBoundarySubject"
    chunks = build_multipart_body(boundary, "test.pdf", b"X" * 65536)
    total_length = sum(len(chunk) for chunk in chunks)

    invalid_claim_tokens = [
        jwt.encode({"type": "access"}, settings.jwt_secret, algorithm="HS256"),
        jwt.encode(
            {"sub": "not-an-integer", "type": "access"}, settings.jwt_secret, algorithm="HS256"
        ),
    ]

    for token_string in invalid_claim_tokens:
        request_headers = [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {token_string}".encode("ascii")),
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
            (b"content-length", str(total_length).encode("ascii")),
        ]
        (
            status_code,
            unused_headers,
            response_body,
            receive_count,
            bytes_consumed,
        ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)
        assert status_code == 401
        assert receive_count == 0
        assert bytes_consumed == 0
        assert b"Invalid or expired token" in response_body


@pytest.mark.asyncio
async def test_case_insensitive_bearer_accepted_by_guard():
    """Verify bearer scheme prefix is recognized case-insensitively by upload guard."""

    async def downstream_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"downstream_ok", "more_body": False})

    guard_instance = UploadGuardMiddleware(app=downstream_app)
    valid_token_string = create_access_token({"sub": "1"})

    for prefix in ["bearer ", "BEARER ", "Bearer "]:
        request_headers = [
            (b"host", b"testserver"),
            (b"authorization", f"{prefix}{valid_token_string}".encode("ascii")),
        ]
        (
            status_code,
            unused_headers,
            response_body,
            receive_count,
            bytes_consumed,
        ) = await invoke_asgi_request(
            guard_instance, "POST", "/api/documents/upload", request_headers, []
        )
        assert status_code == 200
        assert response_body == b"downstream_ok"


@pytest.mark.asyncio
async def test_legacy_token_without_type_claim_accepted(client, test_user, db):
    """Verify legacy tokens without type claim are accepted for upload."""
    legacy_payload = {"sub": str(test_user.id)}
    legacy_token_string = jwt.encode(legacy_payload, settings.jwt_secret, algorithm="HS256")

    with patch("app.routers.documents._dispatch_parse"):
        upload_response = await client.post(
            "/api/documents/upload",
            headers={"Authorization": f"Bearer {legacy_token_string}"},
            files={"file": ("legacy.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
        )
    assert upload_response.status_code == 200
    assert upload_response.json()["format"] == "pdf"


@pytest.mark.asyncio
async def test_declared_oversize_rejected_without_consuming_receive(test_user):
    """Verify declared Content-Length exceeding maximum limit returns 413 without reading body."""
    boundary = "----WebKitFormBoundaryDeclaredOver"
    chunks = build_multipart_body(boundary, "large.pdf", b"X" * 1024)
    valid_token_string = create_access_token({"sub": str(test_user.id)})

    oversize_declared_length = 350 * 1024 * 1024
    request_headers = [
        (b"host", b"testserver"),
        (b"authorization", f"Bearer {valid_token_string}".encode("ascii")),
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
        (b"content-length", str(oversize_declared_length).encode("ascii")),
    ]

    (
        status_code,
        unused_headers,
        response_body,
        receive_count,
        bytes_consumed,
    ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)

    assert status_code == 413
    assert receive_count == 0
    assert bytes_consumed == 0
    assert b"File too large" in response_body


@pytest.mark.asyncio
async def test_chunked_oversize_rejected_safely(test_user):
    """Verify streaming chunked upload exceeding limit halts receive and returns 413."""
    boundary = "----WebKitFormBoundaryChunkedOver"
    valid_token_string = create_access_token({"sub": str(test_user.id)})

    chunk_megabytes = b"M" * (1536 * 1024)
    part_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="chunked.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")
    part_footer = f"\r\n--{boundary}--\r\n".encode("utf-8")

    chunks = [
        part_header + chunk_megabytes,
        chunk_megabytes,
        chunk_megabytes,
        part_footer,
    ]

    request_headers = [
        (b"host", b"testserver"),
        (b"authorization", f"Bearer {valid_token_string}".encode("ascii")),
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode("ascii")),
    ]

    with patch.object(settings, "upload_max_size_mb", 1):
        (
            status_code,
            unused_headers,
            response_body,
            receive_count,
            bytes_consumed,
        ) = await invoke_asgi_request(app, "POST", "/api/documents/upload", request_headers, chunks)

    assert status_code == 413
    assert receive_count == 2
    assert bytes_consumed < sum(len(chunk) for chunk in chunks)
    assert b"File too large" in response_body


@pytest.mark.asyncio
async def test_valid_authenticated_pdf_upload_succeeds(client, auth_header, db):
    """Verify authenticated valid PDF upload succeeds end-to-end through middleware stack."""
    with patch("app.routers.documents._dispatch_parse"):
        upload_response = await client.post(
            "/api/documents/upload",
            headers=auth_header,
            files={"file": ("guard_verified.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
        )

    assert upload_response.status_code == 200
    response_data = upload_response.json()
    assert response_data["title"] == "guard_verified.pdf"
    assert response_data["format"] == "pdf"

    persisted_document = db.query(Document).filter(Document.id == response_data["id"]).first()
    assert persisted_document is not None
    assert persisted_document.title == "guard_verified.pdf"


@pytest.mark.asyncio
async def test_non_upload_routes_completely_unaffected(client, auth_header):
    """Verify non-upload endpoints and ordinary routes are unaffected by upload guard."""
    health_response = await client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "healthy"}

    health_post_response = await client.post("/health")
    assert health_post_response.status_code == 405

    unauthenticated_documents_response = await client.get("/api/documents")
    assert unauthenticated_documents_response.status_code == 401

    authenticated_documents_response = await client.get("/api/documents", headers=auth_header)
    assert authenticated_documents_response.status_code == 200


@pytest.mark.asyncio
async def test_upload_url_route_reaches_router_unaffected(client, auth_header, second_user, db):
    """Verify upload-url route is not intercepted by upload guard and reaches its own handler."""
    from app.models import Notebook

    foreign_notebook = Notebook(user_id=second_user.id, name="ForeignNotebook")
    db.add(foreign_notebook)
    db.commit()

    upload_url_response = await client.post(
        "/api/documents/upload-url",
        headers=auth_header,
        data={"url": "http://127.0.0.1/doc.pdf", "notebook_id": str(foreign_notebook.id)},
    )
    assert upload_url_response.status_code == 404
    assert upload_url_response.json()["detail"] == "Notebook not found"


@pytest.mark.asyncio
async def test_middleware_constructor_keyword_and_raw_asgi():
    """Verify middleware constructor accepts app keyword and handles raw ASGI safely."""

    async def minimal_raw_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        while True:
            incoming_message = await receive()
            if not incoming_message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"raw_ok", "more_body": False})

    guard_instance = UploadGuardMiddleware(app=minimal_raw_app)
    valid_token_string = create_access_token({"sub": "42"})

    request_headers = [
        (b"host", b"testserver"),
        (b"authorization", f"Bearer {valid_token_string}".encode("ascii")),
        (b"content-type", b"multipart/form-data; boundary=testboundary"),
    ]
    chunks = [b"A" * (1536 * 1024), b"B" * (1536 * 1024), b"C" * (1536 * 1024)]

    with patch.object(settings, "upload_max_size_mb", 1):
        (
            status_code,
            unused_headers,
            response_body,
            receive_count,
            unused_bytes,
        ) = await invoke_asgi_request(
            guard_instance, "POST", "/api/documents/upload", request_headers, chunks
        )

    assert status_code == 413
    assert receive_count == 2
    assert b"File too large" in response_body


@pytest.mark.asyncio
async def test_websocket_scope_passes_through_unmodified():
    """Verify non-http scopes such as websocket bypass upload guard untouched."""
    passed_to_app = False

    async def mock_ws_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        nonlocal passed_to_app
        passed_to_app = True

    guard_instance = UploadGuardMiddleware(app=mock_ws_app)
    websocket_scope = {"type": "websocket", "path": "/api/ws"}

    await guard_instance(websocket_scope, None, None)
    assert passed_to_app is True
