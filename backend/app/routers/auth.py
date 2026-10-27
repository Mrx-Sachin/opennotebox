"""Authentication router."""
import os
import time
from pathlib import Path
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from sqlalchemy import delete, func, or_, text, update
from sqlalchemy.orm import Session
from loguru import logger

from app.config import settings
from app.database import get_db
from app.models import (
    Bookmark,
    Conversation,
    Document,
    Flashcard,
    FlashcardReviewLog,
    LibraryEntry,
    Message,
    Note,
    Notebook,
    Podcast,
    Summary,
    TTSCache,
    User,
    UserSettings,
)
from app.schemas import UserCreate, UserLogin, UserResponse, TokenResponse, RefreshRequest, UserUpdate
from app.services.auth_service import (
    verify_password_async,
    get_password_hash_async,
    create_access_token,
    create_refresh_token,
    get_current_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# --- Brute-force rate limiting -------------------------------------------------
# Each failed login is a full bcrypt verification, so an unthrottled
# /api/auth/login on a reachable instance is a password-listing engine and a
# CPU burn at the same time. Sliding-window counters, in-memory, on the same
# premise as the chat limiter in chat.py: the API is a single uvicorn process,
# so per-process state IS per-deployment state.
#
# Login is keyed (client_ip, username) so one IP spraying many usernames is
# also capped, not just one username from many IPs. Counting ALL attempts
# (not just failures) is deliberate: the cost to deny is the bcrypt work,
# which happens before we know the answer is wrong.
_LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", 10))
_LOGIN_WINDOW = 60.0
_REGISTER_MAX_ATTEMPTS = 5
_REGISTER_WINDOW = 900.0
_login_buckets: dict[tuple[str, str], deque] = defaultdict(deque)
_register_buckets: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(bucket: deque, limit: int, window: float, detail: str) -> None:
    now = time.monotonic()
    while bucket and bucket[0] <= now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail=detail)
    bucket.append(now)


# defaultdict(deque) creates an entry on every unseen key and nothing ever
# removes them, so an anonymous attacker spraying random usernames would grow
# the bucket dict without bound (the DoS the limiter exists to prevent, moved
# from bcrypt-CPU into dict-RAM). Once past a soft ceiling, drop every bucket
# whose newest timestamp is already outside the window; the scan is trivial at
# this scale, so a plain size check is enough back-pressure.
_BUCKET_SOFT_CEILING = 10_000


def _prune_stale_buckets(buckets: dict, window: float) -> None:
    if len(buckets) <= _BUCKET_SOFT_CEILING:
        return
    cutoff = time.monotonic() - window
    for key in [k for k, dq in buckets.items() if not dq or dq[-1] <= cutoff]:
        del buckets[key]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Transaction-scoped advisory lock key for the first-user bootstrap. Held from
# the "is this the first account?" check through the INSERT, so two parallel
# /register calls on an empty instance can't both observe an empty table and
# both mint an admin. Released automatically when the transaction commits.
_BOOTSTRAP_LOCK_KEY = 0x0E5B7F01


def _acquire_bootstrap_lock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BOOTSTRAP_LOCK_KEY})


@router.post("/register", response_model=UserResponse)
async def register(
    request: Request,
    user_data: UserCreate,
    db: Session = Depends(get_db),
):
    # Registration is closed by default (config.allow_registration); when it
    # IS open, a strict per-IP cap means a burst of signups cannot race or
    # spam the endpoint, which matters most during the bootstrap window.
    _check_rate_limit(
        _register_buckets[_client_ip(request)],
        _REGISTER_MAX_ATTEMPTS,
        _REGISTER_WINDOW,
        "Too many registration attempts. Please try again later.",
    )
    _prune_stale_buckets(_register_buckets, _REGISTER_WINDOW)
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="Registration is closed on this instance")
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        # Unlike login, register deliberately confirms whether a username
        # exists - there's no separate "check availability" endpoint, and
        # a user picking a name needs to know to try another one. This is
        # the standard tradeoff registration forms make everywhere.
        raise HTTPException(status_code=400, detail="Username already taken")
    # Hash before taking the lock: bcrypt is the expensive step (~100ms+),
    # and the advisory lock below should only be held across the cheap
    # re-check + insert, not the hash.
    password_hash = await get_password_hash_async(user_data.password)
    # Bootstrap: the very first account on an instance is the admin. Admin
    # gates (shared LLM backend registry, user management) would otherwise
    # have nobody who can ever grant the role.
    #
    # The check-then-insert is racy without the lock: two parallel
    # registrations against an empty table both see "no users" and both
    # insert an admin. The advisory lock serialises bootstrap inserts;
    # the re-check inside the lock makes the loser observe the winner's
    # row and register as a plain user.
    _acquire_bootstrap_lock(db)
    # Re-check inside the lock: a concurrent registration may have inserted
    # this exact username (or the first user, for the is_first decision) in
    # the gap between the pre-lock check and now. The re-check turns what
    # would be an IntegrityError 500 into a clean 400, and makes the
    # bootstrap decision see committed state.
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    is_first_user = db.query(User).first() is None
    user = User(
        username=user_data.username,
        password_hash=password_hash,
        is_admin=is_first_user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    credentials: UserLogin,
    db: Session = Depends(get_db),
):
    # Two independent caps on the same window: one per (IP, username) to stop
    # credential guessing, one per IP to stop one address spraying a username
    # list. See the limiter block at the top of this file.
    _check_rate_limit(
        _login_buckets[(_client_ip(request), credentials.username)],
        _LOGIN_MAX_ATTEMPTS,
        _LOGIN_WINDOW,
        "Too many login attempts. Please wait a minute and try again.",
    )
    _check_rate_limit(
        _login_buckets[(_client_ip(request), "")],
        _LOGIN_MAX_ATTEMPTS * 5,
        _LOGIN_WINDOW,
        "Too many login attempts from this address. Please wait a minute.",
    )
    _prune_stale_buckets(_login_buckets, _LOGIN_WINDOW)
    user = db.query(User).filter(User.username == credentials.username).first()
    if not user or not await verify_password_async(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    access_token = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    from app.services.auth_service import decode_token

    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user_id = payload.get("sub")
    # sub is attacker-controlled input inside a validly-signed token only if
    # the secret leaked; guard the int() conversion so a malformed/tampered
    # claim 400s instead of 500ing.
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    access_token = create_access_token({"sub": str(user.id)})
    new_refresh = create_refresh_token({"sub": str(user.id)})
    return TokenResponse(access_token=access_token, refresh_token=new_refresh)


def get_current_user_from_token(
    authorization: str = Header(""),
    db: Session = Depends(get_db),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split("Bearer ", 1)[1]
    user = get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user_from_token)):
    return user


def require_admin(user: User = Depends(get_current_user_from_token)):
    """Require admin privilege for sensitive operations."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privilege required")
    return user


@router.get("/users", response_model=list[UserResponse])
def list_users(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all users (admin only)."""
    return db.query(User).order_by(User.id).all()


@router.get("/users/stats")
def user_stats(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-user storage and document stats (admin only)."""
    rows = (
        db.query(
            User.id,
            User.username,
            User.is_admin,
            User.created_at,
            func.coalesce(func.count(Document.id), 0).label("doc_count"),
            func.coalesce(func.sum(Document.file_size), 0).label("total_bytes"),
        )
        .outerjoin(LibraryEntry, LibraryEntry.user_id == User.id)
        .outerjoin(Document, Document.id == LibraryEntry.document_id)
        .group_by(User.id)
        .order_by(User.id)
        .all()
    )
    result = []
    for r in rows:
        docs = (
            db.query(
                Document.id,
                Document.title,
                Document.file_size,
                Document.num_pages,
                Document.format,
                Document.processing_status,
                Document.created_at,
            )
            .join(LibraryEntry, LibraryEntry.document_id == Document.id)
            .filter(LibraryEntry.user_id == r.id)
            .order_by(Document.created_at.desc())
            .all()
        )
        result.append({
            "id": r.id,
            "username": r.username,
            "is_admin": r.is_admin,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "doc_count": r.doc_count,
            "total_bytes": r.total_bytes,
            "documents": [
                {
                    "id": d.id,
                    "title": d.title,
                    "file_size": d.file_size,
                    "num_pages": d.num_pages,
                    "format": d.format,
                    "processing_status": d.processing_status,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in docs
            ],
        })
    return result


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    update: UserUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update user (admin only - can promote/demote)."""
    target = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if update.is_admin is not None:
        target.is_admin = update.is_admin
    db.commit()
    db.refresh(target)
    return target


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a user and their personal data while retaining library assets."""
    target = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    asset_ids = {
        doc_id
        for (doc_id,) in db.query(Document.id).filter(
            or_(Document.user_id == user_id, Document.shared_by_user_id == user_id)
        ).all()
    }
    asset_ids.update(
        doc_id
        for (doc_id,) in db.query(LibraryEntry.document_id).filter(
            LibraryEntry.user_id == user_id
        ).all()
    )
    if asset_ids:
        db.query(Document.id).filter(Document.id.in_(asset_ids)).order_by(
            Document.id
        ).with_for_update().all()

    # Documents belong to the shared library independently of their uploader.
    # Detach the uploader and remove memberships before deleting the account so
    # the User.documents ORM cascade cannot delete source files for other users.
    db.query(LibraryEntry).filter(LibraryEntry.user_id == user_id).delete(
        synchronize_session=False
    )
    notebook_ids = db.query(Notebook.id).filter(Notebook.user_id == user_id)
    db.execute(
        update(Document)
        .where(Document.notebook_id.in_(notebook_ids))
        .values(notebook_id=None)
    )
    db.query(Notebook).filter(Notebook.user_id == user_id).delete(synchronize_session=False)
    db.execute(
        update(Document)
        .where(Document.user_id == user_id)
        .values(user_id=None)
    )
    if hasattr(Document, "shared_by_user_id"):
        db.execute(
            update(Document)
            .where(Document.shared_by_user_id == user_id)
            .values(shared_by_user_id=None, is_shared=False)
        )

    podcast_paths = [
        path for (path,) in db.query(Podcast.audio_path).filter(
            Podcast.user_id == user_id,
            Podcast.audio_path.isnot(None),
        ).all()
    ]
    tts_paths = [
        path for (path,) in db.query(TTSCache.audio_path).filter(
            TTSCache.user_id == user_id,
            TTSCache.audio_path.isnot(None),
        ).all()
    ]

    conversations = db.query(Conversation.id).filter(Conversation.user_id == user_id)
    db.query(Message).filter(Message.conversation_id.in_(conversations)).delete(
        synchronize_session=False
    )
    flashcards = db.query(Flashcard.id).filter(Flashcard.user_id == user_id)
    db.query(FlashcardReviewLog).filter(FlashcardReviewLog.flashcard_id.in_(flashcards)).delete(
        synchronize_session=False
    )
    db.query(Flashcard).filter(Flashcard.user_id == user_id).delete(synchronize_session=False)
    db.query(Note).filter(Note.user_id == user_id).delete(synchronize_session=False)
    db.query(Bookmark).filter(Bookmark.user_id == user_id).delete(synchronize_session=False)
    db.query(Podcast).filter(Podcast.user_id == user_id).delete(synchronize_session=False)
    db.query(TTSCache).filter(TTSCache.user_id == user_id).delete(synchronize_session=False)
    db.query(Summary).filter(Summary.user_id == user_id).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(UserSettings).filter(UserSettings.user_id == user_id).delete(
        synchronize_session=False
    )

    # Core DELETE bypasses User.documents' ORM delete-orphan cascade. Database
    # foreign keys still clean up ordinary account-owned rows such as notebooks.
    db.execute(delete(User).where(User.id == user_id))
    db.commit()

    for path in podcast_paths + tts_paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    if asset_ids:
        from app.tasks.asset_tasks import collect_unreferenced_asset

        for asset_id in asset_ids:
            try:
                collect_unreferenced_asset.apply_async(
                    args=[asset_id],
                    countdown=300,
                )
            except Exception as exc:
                logger.warning(
                    f"Could not queue cleanup for detached asset {asset_id}: {exc}"
                )
    return {"detail": "User deleted"}


@router.post("/users", response_model=UserResponse)
async def admin_create_user(
    user_data: UserCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a user (admin only). Bypasses rate limits and registration gate."""
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
    password_hash = await get_password_hash_async(user_data.password)
    user = User(username=user_data.username, password_hash=password_hash, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
