"""Auth service with JWT tokens and bcrypt password hashing."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User


def _check_password_sync(plain: bytes, hashed: bytes) -> bool:
    try:
        return bcrypt.checkpw(plain, hashed)
    except Exception:
        return False


def _hash_password_sync(password: bytes) -> str:
    return bcrypt.hashpw(password, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _check_password_sync(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    return await asyncio.to_thread(verify_password, plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return _hash_password_sync(password.encode("utf-8"))


async def get_password_hash_async(password: str) -> str:
    return await asyncio.to_thread(get_password_hash, password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=settings.jwt_expiry_hours))
    # Tagged "access" (mirroring the refresh token's "type": "refresh") so a
    # stolen/leaked refresh token can't be replayed as a bearer access token -
    # previously get_current_user() accepted either kind of token.
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")


def create_refresh_token(data: dict) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    data.update({"exp": expire, "type": "refresh"})
    return jwt.encode(data, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        return None


def get_current_user(db: Session, token: str) -> Optional[User]:
    payload = decode_token(token)
    if payload is None:
        return None
    # Reject refresh tokens presented as bearer tokens - see create_access_token.
    # Tokens issued before this change have no "type" claim; treat those as
    # access tokens too so existing sessions aren't force-logged-out.
    if payload.get("type") not in (None, "access"):
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    return db.query(User).filter(User.id == int(user_id)).first()
