"""ASGI upload guard middleware."""

from typing import Any, Callable

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.config import settings
from app.services.auth_service import decode_token

MULTIPART_OVERHEAD_BYTES: int = 1024 * 1024


class UploadSizeExceededError(HTTPException):
    """Raised when an upload request exceeds the maximum permitted byte size."""

    def __init__(self, detail_message: str) -> None:
        super().__init__(status_code=413, detail=detail_message)


class UploadGuardMiddleware:
    """Pure-ASGI pre-body guard for document uploads."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[dict[str, Any]], Any],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        request_path = scope.get("path", "").rstrip("/")
        if request_path != "/api/documents/upload":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        raw_authorization = request_headers.get("authorization")
        if not raw_authorization:
            response = JSONResponse({"detail": "Missing or invalid token"}, status_code=401)
            await response(scope, receive, send)
            return

        scheme_name, separator, token_value = raw_authorization.partition(" ")
        if scheme_name.lower() != "bearer" or not separator or not token_value.strip():
            response = JSONResponse({"detail": "Missing or invalid token"}, status_code=401)
            await response(scope, receive, send)
            return

        token_string = token_value.strip()
        token_payload = decode_token(token_string)
        if token_payload is None:
            response = JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
            await response(scope, receive, send)
            return

        if token_payload.get("type") not in (None, "access"):
            response = JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
            await response(scope, receive, send)
            return

        user_id = token_payload.get("sub")
        if user_id is None:
            response = JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
            await response(scope, receive, send)
            return

        try:
            int(user_id)
        except (ValueError, TypeError):
            response = JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
            await response(scope, receive, send)
            return

        max_upload_bytes = settings.upload_max_size_mb * 1024 * 1024
        total_limit_bytes = max_upload_bytes + MULTIPART_OVERHEAD_BYTES
        detail_message = f"File too large (max {settings.upload_max_size_mb} MB)"

        raw_content_length = request_headers.get("content-length")
        if raw_content_length is not None:
            try:
                declared_content_length = int(raw_content_length.strip())
                if declared_content_length > total_limit_bytes:
                    response = JSONResponse({"detail": detail_message}, status_code=413)
                    await response(scope, receive, send)
                    return
            except (ValueError, TypeError):
                pass

        total_received_bytes = 0
        response_started = False

        async def bounded_receive() -> dict[str, Any]:
            nonlocal total_received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                chunk_body = message.get("body", b"")
                total_received_bytes += len(chunk_body)
                if total_received_bytes > total_limit_bytes:
                    raise UploadSizeExceededError(detail_message)
            return message

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracking_send)
        except UploadSizeExceededError as size_error:
            if not response_started:
                response = JSONResponse({"detail": size_error.detail}, status_code=413)
                await response(scope, receive, send)
