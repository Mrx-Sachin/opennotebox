from __future__ import annotations

import io
import socket
from unittest.mock import patch, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.routers.documents import _validate_download_url
from app.services.auth_service import get_password_hash


class TestAdminProtection:
    async def test_update_lone_admin_unchanged_or_promoted_allowed(self, client, admin_auth_header, admin_user):
        response_confirm_admin = await client.patch(
            f"/api/auth/users/{admin_user.id}",
            headers=admin_auth_header,
            json={"is_admin": True},
        )
        assert response_confirm_admin.status_code == 200
        assert response_confirm_admin.json()["is_admin"] is True

        response_empty_update = await client.patch(
            f"/api/auth/users/{admin_user.id}",
            headers=admin_auth_header,
            json={},
        )
        assert response_empty_update.status_code == 200

    async def test_demote_lone_admin_denied(self, client, admin_auth_header, admin_user):
        response_demote = await client.patch(
            f"/api/auth/users/{admin_user.id}",
            headers=admin_auth_header,
            json={"is_admin": False},
        )
        assert response_demote.status_code == 400
        assert "Cannot demote the last remaining administrator" in response_demote.json()["detail"]

    async def test_delete_lone_admin_denied(self, client, admin_auth_header, admin_user):
        response_delete = await client.delete(
            f"/api/auth/users/{admin_user.id}",
            headers=admin_auth_header,
        )
        assert response_delete.status_code == 400
        assert "Cannot delete the last remaining administrator" in response_delete.json()["detail"]

    async def test_multi_admin_demote_and_delete_allowed(self, client, admin_auth_header, admin_user, db: Session):
        second_admin = User(
            username="secondadmin",
            password_hash=get_password_hash("password12345"),
            is_admin=True,
        )
        db.add(second_admin)
        db.commit()
        db.refresh(second_admin)

        response_demote = await client.patch(
            f"/api/auth/users/{second_admin.id}",
            headers=admin_auth_header,
            json={"is_admin": False},
        )
        assert response_demote.status_code == 200
        assert response_demote.json()["is_admin"] is False

        response_delete = await client.delete(
            f"/api/auth/users/{second_admin.id}",
            headers=admin_auth_header,
        )
        assert response_delete.status_code == 200


class TestSSRFProtection:
    def test_ssrf_blocks_loopback(self):
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]):
            with pytest.raises(HTTPException) as exception_info:
                _validate_download_url("http://127.0.0.1/test.pdf")
            assert exception_info.value.status_code == 400

    def test_ssrf_blocks_private_rfc1918(self):
        private_addresses = ["10.0.1.5", "172.16.50.1", "192.168.1.100"]
        for target_ip in private_addresses:
            with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (target_ip, 80))]):
                with pytest.raises(HTTPException) as exception_info:
                    _validate_download_url(f"http://{target_ip}/doc.pdf")
                assert exception_info.value.status_code == 400

    def test_ssrf_blocks_carrier_grade_nat_and_tailscale(self):
        tailscale_addresses = ["100.64.0.1", "100.69.125.79", "100.71.237.116"]
        for target_ip in tailscale_addresses:
            with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (target_ip, 80))]):
                with pytest.raises(HTTPException) as exception_info:
                    _validate_download_url(f"http://{target_ip}/doc.pdf")
                assert exception_info.value.status_code == 400

    def test_ssrf_blocks_link_local_and_metadata(self):
        metadata_ip = "169.254.169.254"
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (metadata_ip, 80))]):
            with pytest.raises(HTTPException) as exception_info:
                _validate_download_url(f"http://{metadata_ip}/latest/meta-data/")
            assert exception_info.value.status_code == 400

    def test_ssrf_blocks_ipv4_mapped_ipv6(self):
        mapped_addresses = ["::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:100.69.125.79", "::ffff:169.254.169.254"]
        for target_ip in mapped_addresses:
            with patch("socket.getaddrinfo", return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (target_ip, 80, 0, 0))]):
                with pytest.raises(HTTPException) as exception_info:
                    _validate_download_url("http://attacker.example/doc.pdf")
                assert exception_info.value.status_code == 400


    def test_ssrf_opt_in_allows_private_and_loopback_but_blocks_metadata(self, monkeypatch):
        from app.routers import documents
        monkeypatch.setattr(documents.settings, "download_allow_private_hosts", True)

        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]):
            pinned_loopback = _validate_download_url("http://127.0.0.1/doc.pdf")
            assert "127.0.0.1:80" in pinned_loopback

        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))]):
            pinned_private = _validate_download_url("http://10.0.0.5/doc.pdf")
            assert "10.0.0.5:80" in pinned_private

        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]):
            with pytest.raises(HTTPException) as exception_info:
                _validate_download_url("http://169.254.169.254/latest/meta-data/")
            assert exception_info.value.status_code == 400
            assert "blocked internal address range" in exception_info.value.detail

    def test_ssrf_allows_public_ip(self):
        public_ip = "8.8.8.8"
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (public_ip, 80))]):
            pinned_addresses = _validate_download_url(f"http://{public_ip}/doc.pdf")
            assert f"{public_ip}:80" in pinned_addresses
            assert public_ip in pinned_addresses[f"{public_ip}:80"]


class TestUploadValidation:
    @patch("app.routers.documents._dispatch_parse")
    async def test_upload_pdf_valid_magic_bytes_accepted(self, mock_dispatch, client, auth_header):
        response_upload = await client.post(
            "/api/documents/upload",
            headers=auth_header,
            files={"file": ("sample.pdf", io.BytesIO(b"%PDF-1.7 valid pdf"), "application/pdf")},
        )
        assert response_upload.status_code == 200
        assert response_upload.json()["format"] == "pdf"

    @patch("app.routers.documents._dispatch_parse")
    async def test_upload_pdf_invalid_magic_bytes_rejected(self, mock_dispatch, client, auth_header):
        response_upload = await client.post(
            "/api/documents/upload",
            headers=auth_header,
            files={"file": ("fake.pdf", io.BytesIO(b"<html><body>Not a PDF</body></html>"), "application/pdf")},
        )
        assert response_upload.status_code == 400
        assert "File content does not match declared pdf format" in response_upload.json()["detail"]

    @patch("app.routers.documents._dispatch_parse")
    async def test_upload_docx_invalid_magic_bytes_rejected(self, mock_dispatch, client, auth_header):
        response_upload = await client.post(
            "/api/documents/upload",
            headers=auth_header,
            files={"file": ("fake.docx", io.BytesIO(b"Plain text masquerading as docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert response_upload.status_code == 400
        assert "File content does not match declared docx format" in response_upload.json()["detail"]


class TestSecurityHeadersMiddleware:
    async def test_security_headers_present_on_api_responses(self, client):
        response_health = await client.get("/health")
        assert response_health.status_code == 200
        assert response_health.headers.get("x-content-type-options") == "nosniff"
        assert response_health.headers.get("x-frame-options") == "DENY"
        assert response_health.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "camera=()" in response_health.headers.get("permissions-policy", "")


class TestConfigurationSecurity:
    def test_cors_private_networks_toggle(self):
        settings_hardened = Settings(jwt_secret="dummy_secret_32_characters_minimum", cors_allow_private_networks=False)
        assert settings_hardened.cors_origin_regex is None

        settings_permissive = Settings(jwt_secret="dummy_secret_32_characters_minimum", cors_allow_private_networks=True)
        assert settings_permissive.cors_origin_regex is not None


class TestSSRFRedirectProtection:
    async def test_redirect_to_private_ip_is_blocked(self, client, auth_header):
        initial_url = "http://8.8.8.8/public.pdf"
        mock_response = httpx.Response(302, headers={"Location": "http://127.0.0.1/secret.pdf"}, request=httpx.Request("GET", initial_url))

        with patch("app.routers.documents._validate_download_url") as mock_validate:
            mock_validate.side_effect = [
                {"8.8.8.8:80": ("8.8.8.8",)},
                HTTPException(status_code=400, detail="This host points at a blocked internal address range"),
            ]
            with patch("app.routers.documents._pinned_client") as mock_client_factory:
                mock_stream_ctx = MagicMock()
                mock_stream_ctx.__enter__.return_value = mock_response
                mock_stream_ctx.__exit__.return_value = None
                mock_inner_client = MagicMock()
                mock_inner_client.stream.return_value = mock_stream_ctx
                mock_client_factory.return_value.__enter__.return_value = mock_inner_client

                response_upload = await client.post(
                    "/api/documents/upload-url",
                    headers=auth_header,
                    data={"url": initial_url},
                )
                assert response_upload.status_code == 400
                assert "blocked internal address range" in response_upload.json()["detail"]

    async def test_redirect_missing_location_is_blocked(self, client, auth_header):
        initial_url = "http://8.8.8.8/public.pdf"
        mock_response = httpx.Response(302, headers={}, request=httpx.Request("GET", initial_url))

        with patch("app.routers.documents._validate_download_url") as mock_validate:
            mock_validate.return_value = {"8.8.8.8:80": ("8.8.8.8",)}
            with patch("app.routers.documents._pinned_client") as mock_client_factory:
                mock_stream_ctx = MagicMock()
                mock_stream_ctx.__enter__.return_value = mock_response
                mock_stream_ctx.__exit__.return_value = None
                mock_inner_client = MagicMock()
                mock_inner_client.stream.return_value = mock_stream_ctx
                mock_client_factory.return_value.__enter__.return_value = mock_inner_client

                response_upload = await client.post(
                    "/api/documents/upload-url",
                    headers=auth_header,
                    data={"url": initial_url},
                )
                assert response_upload.status_code == 400
                assert "Redirect missing location header" in response_upload.json()["detail"]


class TestAppMiddlewareStackAndClientIP:
    def test_app_build_middleware_stack_succeeds(self):
        from app.main import app
        stack = app.build_middleware_stack()
        assert stack is not None

    def test_client_ip_does_not_trust_spoofed_cf_connecting_ip(self):
        from fastapi import Request
        from app.routers.auth import _client_ip

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [
                (b"cf-connecting-ip", b"1.2.3.4"),
                (b"x-forwarded-for", b"5.6.7.8"),
            ],
            "client": ("192.168.1.50", 12345),
        }
        mock_request = Request(scope)
        resolved_ip = _client_ip(mock_request)
        assert resolved_ip == "192.168.1.50"
        assert resolved_ip != "1.2.3.4"

    def test_client_ip_handles_unknown(self):
        from fastapi import Request
        from app.routers.auth import _client_ip

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "client": None,
        }
        mock_request = Request(scope)
        resolved_ip = _client_ip(mock_request)
        assert resolved_ip == "unknown"
