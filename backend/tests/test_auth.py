"""Tests for authentication service and auth router endpoints.

Covers: register, login, token refresh, /me, admin operations,
token expiry, and auth_service utility functions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import jwt

from app.services.auth_service import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)

# JWT secret matching conftest.py
_JWT_SECRET = "test_secret_32_chars_long_for_testing!"


class TestAuthService:
    """Unit tests for auth_service utility functions."""

    def test_password_hash_and_verify(self) -> None:
        raw = "mysecretpassword"
        hashed = get_password_hash(raw)
        assert verify_password(raw, hashed)
        assert not verify_password("wrongpassword", hashed)

    def test_password_hash_is_different_each_time(self) -> None:
        """bcrypt uses random salts, so two hashes should differ."""
        h1 = get_password_hash("password")
        h2 = get_password_hash("password")
        assert h1 != h2
        assert verify_password("password", h1)
        assert verify_password("password", h2)

    def test_verify_password_handles_bad_hash(self) -> None:
        """Malformed hashes should return False, not raise."""
        assert not verify_password("anything", "not-a-valid-hash")

    def test_access_token_contains_type(self) -> None:
        token = create_access_token({"sub": "42"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["type"] == "access"
        assert "exp" in payload

    def test_refresh_token_contains_type(self) -> None:
        token = create_refresh_token({"sub": "42"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["type"] == "refresh"

    def test_decode_token_returns_none_on_bad_token(self) -> None:
        assert decode_token("garbage.token.here") is None

    def test_decode_token_returns_none_on_empty(self) -> None:
        assert decode_token("") is None

    def test_get_current_user_finds_user(self, db, test_user) -> None:
        token = create_access_token({"sub": str(test_user.id)})
        user = get_current_user(db, token)
        assert user is not None
        assert user.id == test_user.id

    def test_get_current_user_none_on_expired(self, db, test_user) -> None:
        payload = {
            "sub": str(test_user.id),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "type": "access",
        }
        token = jwt.encode(payload, _JWT_SECRET, algorithm="HS256")
        assert get_current_user(db, token) is None

    def test_get_current_user_none_on_refresh_as_bearer(
        self, db, test_user
    ) -> None:
        """Refresh tokens must be rejected as bearer tokens."""
        token = create_refresh_token({"sub": str(test_user.id)})
        assert get_current_user(db, token) is None

    def test_get_current_user_none_on_missing_sub(self) -> None:
        payload = {
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "type": "access",
        }
        token = jwt.encode(payload, _JWT_SECRET, algorithm="HS256")
        from app.database import SessionLocal
        with SessionLocal() as db:
            assert get_current_user(db, token) is None

    def test_get_current_user_legacy_token_no_type(
        self, db, test_user
    ) -> None:
        """Tokens without a type claim (legacy) should be accepted as access."""
        payload = {
            "sub": str(test_user.id),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = jwt.encode(payload, _JWT_SECRET, algorithm="HS256")
        user = get_current_user(db, token)
        assert user is not None
        assert user.id == test_user.id


class TestRegisterEndpoint:
    """Integration tests for POST /api/auth/register.

    Registration is closed by default (first registrant becomes admin, so
    open registration on a reachable URL is an ownership handout). These
    tests exercise the register path itself, so they re-enable it explicitly
    via monkeypatch; the closed-default behaviour has its own test below.
    """

    def _open_registration(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "allow_registration", True)

    async def test_register_closed_by_default(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "allow_registration", False)
        resp = await client.post("/api/auth/register", json={
            "username": "newuser",
            "password": "password123",
        })
        assert resp.status_code == 403

    async def test_register_success(self, client, auth_header, monkeypatch):
        self._open_registration(monkeypatch)
        resp = await client.post("/api/auth/register", json={
            "username": "newuser",
            "password": "password123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "newuser"
        assert "password_hash" not in data

    async def test_register_duplicate_username(self, client, auth_header, monkeypatch):
        self._open_registration(monkeypatch)
        await client.post("/api/auth/register", json={
            "username": "duplicate",
            "password": "password123",
        })
        resp = await client.post("/api/auth/register", json={
            "username": "duplicate",
            "password": "password123",
        })
        assert resp.status_code == 400

    async def test_register_short_username(self, client, auth_header):
        resp = await client.post("/api/auth/register", json={
            "username": "ab",
            "password": "password123",
        })
        assert resp.status_code == 422

    async def test_register_short_password(self, client, auth_header):
        resp = await client.post("/api/auth/register", json={
            "username": "newuser",
            "password": "12345",
        })
        assert resp.status_code == 422

    async def test_register_no_password(self, client, auth_header):
        resp = await client.post("/api/auth/register", json={
            "username": "newuser",
        })
        assert resp.status_code == 422

    async def test_register_response_no_password_hash(self, client, auth_header, monkeypatch):
        self._open_registration(monkeypatch)
        resp = await client.post("/api/auth/register", json={
            "username": "safeuser",
            "password": "password123",
        })
        assert resp.status_code == 200
        assert "password" not in resp.json()


class TestLoginEndpoint:
    """Integration tests for POST /api/auth/login."""

    async def test_login_success(self, client, test_user):
        resp = await client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "testpass123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_login_wrong_password(self, client, test_user):
        resp = await client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client):
        resp = await client.post("/api/auth/login", json={
            "username": "doesnotexist",
            "password": "password",
        })
        assert resp.status_code == 401


class TestRefreshEndpoint:
    """Integration tests for POST /api/auth/refresh."""

    async def test_refresh_success(self, client, refresh_token_value: str):
        resp = await client.post("/api/auth/refresh", json={
            "refresh_token": refresh_token_value,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_expired_token(self, client, expired_token: str):
        resp = await client.post("/api/auth/refresh", json={
            "refresh_token": expired_token,
        })
        assert resp.status_code == 401

    async def test_refresh_access_token_as_refresh(self, client, access_token: str):
        resp = await client.post("/api/auth/refresh", json={
            "refresh_token": access_token,
        })
        assert resp.status_code == 401

    async def test_refresh_garbage_token(self, client):
        resp = await client.post("/api/auth/refresh", json={
            "refresh_token": "not-a-real-token",
        })
        assert resp.status_code == 401


class TestMeEndpoint:
    """Integration tests for GET /api/auth/me."""

    async def test_me_success(self, client, auth_header):
        resp = await client.get("/api/auth/me", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"
        assert data["is_admin"] is False

    async def test_me_no_token(self, client):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    async def test_me_expired_token(self, client, expired_token: str):
        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    async def test_me_invalid_token(self, client):
        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


class TestAdminEndpoints:
    """Integration tests for admin-only endpoints."""

    async def test_list_users_as_admin(self, client, admin_auth_header):
        resp = await client.get("/api/auth/users", headers=admin_auth_header)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_users_as_non_admin(self, client, auth_header):
        resp = await client.get("/api/auth/users", headers=auth_header)
        assert resp.status_code == 403

    async def test_update_user_as_admin(self, client, admin_auth_header, test_user):
        resp = await client.patch(
            f"/api/auth/users/{test_user.id}",
            headers=admin_auth_header,
            json={"is_admin": True},
        )
        assert resp.status_code == 200
        assert resp.json()["is_admin"] is True

    async def test_delete_user_as_admin(self, client, admin_auth_header, second_user):
        resp = await client.delete(
            f"/api/auth/users/{second_user.id}",
            headers=admin_auth_header,
        )
        assert resp.status_code == 200

    async def test_update_nonexistent_user(self, client, admin_auth_header):
        resp = await client.patch(
            "/api/auth/users/99999",
            headers=admin_auth_header,
            json={"is_admin": True},
        )
        assert resp.status_code == 404

    async def test_delete_nonexistent_user(self, client, admin_auth_header):
        resp = await client.delete(
            "/api/auth/users/99999",
            headers=admin_auth_header,
        )
        assert resp.status_code == 404
