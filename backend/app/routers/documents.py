"""Document upload, listing, and management router."""
import asyncio
import hashlib
import ipaddress
from datetime import datetime, timezone
import httpx
import logging
import os
import socket
import uuid
from pathlib import Path
from typing import Literal, Optional

import httpcore

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import (User, Document, ExtractedText, Summary, Notebook, LibraryEntry)
from app.schemas import (
    DocumentResponse,
    DocumentLibraryResponse,
    ExtractedTextResponse,
    ReadingProgressIn,
    SectionSummaryGenerateRequest,
    PageSummaryGenerateRequest,
)
from app.routers.auth import get_current_user_from_token
from app.config import settings
from app.tasks.pdf_tasks import parse_pdf_document, parse_txt, parse_md, reparse_pdf_document
from app.tasks.doc_tasks import parse_docx, parse_pptx
from app.tasks.epub_tasks import parse_epub
from app.services.document_sections import ensure_section_stubs
from app.services.library_access import (get_document_for_user, get_library_entry, document_response)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_FORMATS = {"pdf", "txt", "md", "docx", "pptx", "epub"}
ALLOWED_UPLOAD_EXTS = {".pdf", ".txt", ".md", ".docx", ".pptx", ".epub"}


def _stored_filename(original_name: str) -> str:
    """Derive a filesystem-safe, collision-proof name to save an upload
    under. Two problems with just using the original filename directly:

    1. Path traversal / separator smuggling: os.path.basename() strips
       "../" on POSIX, but a client can still send literal backslashes
       (e.g. "..\\..\\etc\\passwd") which basename() does NOT treat as a
       separator on Linux - it would just end up as one odd-looking path
       component, but we scrub it anyway so nothing downstream that might
       treat backslashes specially gets a surprise.
    2. Filename collisions: two uploads named "paper.pdf" (from the same
       user, or across the whole uploads dir) used to silently overwrite
       each other's file on disk while the earlier Document row kept
       pointing at that same file_path - the first upload would silently
       become unreadable/corrupted. A uuid-prefixed disk name makes this
       impossible; the human-readable original name is preserved
       separately as Document.title for display.
    """
    base = os.path.basename(original_name or "upload").replace("\\", "_").lstrip(".")
    ext = os.path.splitext(base)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"


def _write_file(file_path: str, contents: bytes) -> None:
    """Synchronous file write wrapped for asyncio.to_thread (URL imports)."""
    with open(file_path, "wb") as f:
        f.write(contents)


def _read_upload_capped(file: UploadFile, file_path: str, max_bytes: int) -> int:
    """Stream an upload to disk, reading at most max_bytes + a chunk.

    Returns the number of bytes written. Stops reading (and lets the caller
    clean up) as soon as max_bytes is exceeded, so an over-cap upload costs
    O(1) memory and stops the moment it is over. The final chunk may push
    the total a chunk-size past max_bytes; callers compare with `>`, which
    is exact for bodies up to max_bytes.
    """
    written = 0
    with open(file_path, "wb") as dst:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                break  # over the cap; stop reading, caller will reject + delete
            dst.write(chunk)
    return written


def _dispatch_parse(fmt: str, file_path: str, filename: str, doc_id: int) -> None:
    """Enqueue the Celery parse task for a freshly created document.

    Raises if the broker is unreachable so the caller can mark the row as
    failed instead of leaving it stuck at "pending" forever (an orphaned
    row that looks like it's "still processing" but never will).
    """
    if fmt == "pdf":
        parse_pdf_document.delay(doc_id)
    elif fmt == "docx":
        parse_docx.delay(file_path, doc_id)
    elif fmt == "pptx":
        parse_pptx.delay(file_path, doc_id)
    elif fmt == "txt":
        parse_txt.delay(file_path, filename, doc_id)
    elif fmt == "md":
        parse_md.delay(file_path, filename, doc_id)
    elif fmt == "epub":
        parse_epub.delay(file_path, doc_id)

# processing_status values past which extraction has finished and page content
# is available to detect sections from.
_EXTRACTED_STATUSES = {"extracted", "summarizing_pages", "summarizing_sections", "completed"}


def _serialize_documents(db: Session, documents: list[Document], user_id: int) -> list[dict]:
    if not documents:
        return []
    ids = [doc.id for doc in documents]
    entries = {entry.document_id: entry for entry in db.query(LibraryEntry).filter(
        LibraryEntry.user_id == user_id, LibraryEntry.document_id.in_(ids)
    ).all()}
    counts = dict(db.query(LibraryEntry.document_id, func.count(LibraryEntry.id)).filter(
        LibraryEntry.document_id.in_(ids)
    ).group_by(LibraryEntry.document_id).all())
    return [document_response(doc, entries[doc.id], member_count=counts.get(doc.id, 0)) for doc in documents]


def _lock_library_entry(db: Session, user_id: int, doc_id: int) -> LibraryEntry:
    entry = db.query(LibraryEntry).filter(
        LibraryEntry.user_id == user_id, LibraryEntry.document_id == doc_id
    ).with_for_update().first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return entry


def _enqueue_personal_summary(db: Session, summary: Summary, task, *args) -> None:
    summary.status = "generating"
    db.commit()
    try:
        task.delay(*args)
    except Exception:
        summary.status = "failed"
        db.commit()
        logger.exception("Could not queue personal summary %s", summary.id)
        raise HTTPException(status_code=503, detail="Summary processing is temporarily unavailable") from None
    db.refresh(summary)


def _file_fingerprint(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reuse_uploaded_asset(db: Session, asset: Document, user: User, title: str,
                         notebook_id: int | None, original_url: str | None) -> Document:
    # Serialize with publication changes and removal; existing attachments are never moved implicitly.
    asset = db.query(Document).filter(Document.id == asset.id).with_for_update().one_or_none()
    if asset is None:
        return None
    entry = db.query(LibraryEntry).filter(
        LibraryEntry.user_id == user.id, LibraryEntry.document_id == asset.id
    ).first()
    if entry is None:
        entry = LibraryEntry(user_id=user.id, document_id=asset.id, notebook_id=notebook_id,
                             title=title, original_url=original_url, can_publish=True)
        db.add(entry)
    else:
        entry.can_publish = True
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        entry = get_library_entry(db, user.id, asset.id)
        entry.can_publish = True
        db.commit()
    return asset


def _save_uploaded_asset(db: Session, user: User, title: str, fmt: str, file_path: str,
                         file_size: int, notebook_id: int | None, layout_mode: str = "auto",
                         original_url: str | None = None) -> tuple[Document, bool]:
    fingerprint = _file_fingerprint(file_path)
    existing = db.query(Document).filter(Document.content_hash == fingerprint).first()
    if existing is not None:
        reused = _reuse_uploaded_asset(db, existing, user, title, notebook_id, original_url)
        if reused is not None:
            Path(file_path).unlink(missing_ok=True)
            return reused, False
    asset = Document(user_id=user.id, notebook_id=notebook_id, title=title, format=fmt,
                     file_path=file_path, file_size=file_size, content_hash=fingerprint,
                     layout_mode=layout_mode, original_url=original_url, processing_status="pending")
    db.add(asset)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Document).filter(Document.content_hash == fingerprint).first()
        if existing is None:
            Path(file_path).unlink(missing_ok=True)
            raise
        reused = _reuse_uploaded_asset(db, existing, user, title, notebook_id, original_url)
        if reused is None:
            return _save_uploaded_asset(db, user, title, fmt, file_path, file_size, notebook_id,
                                        layout_mode, original_url)
        Path(file_path).unlink(missing_ok=True)
        return reused, False
    db.refresh(asset)
    return asset, True


@router.get("/library", response_model=DocumentLibraryResponse)
def get_library(
    search: str = Query(default="", max_length=200),
    format: Optional[Literal["pdf", "epub", "docx", "pptx", "txt", "md"]] = None,
    shelf: Literal["all", "reading", "unread", "processing", "failed"] = "all",
    sort: Literal["recent", "newest", "title"] = "newest",
    notebook_id: Optional[int] = Query(default=None, ge=1),
    unfiled: bool = False,
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Return a searchable page of the current user's library and shelf counts."""
    if notebook_id is not None and unfiled:
        raise HTTPException(
            status_code=422,
            detail="notebook_id and unfiled cannot be used together",
        )
    library = db.query(Document).join(LibraryEntry, LibraryEntry.document_id == Document.id).filter(LibraryEntry.user_id == user.id)
    if notebook_id is not None:
        owned_notebook = db.query(Notebook.id).filter(
            Notebook.id == notebook_id,
            Notebook.user_id == user.id,
        ).first()
        if owned_notebook is None:
            raise HTTPException(status_code=404, detail="Notebook not found")
        library = library.filter(LibraryEntry.notebook_id == notebook_id)
    elif unfiled:
        library = library.filter(LibraryEntry.notebook_id.is_(None))
    reading = library.filter(LibraryEntry.last_read_at.isnot(None))
    processing = library.filter(
        Document.processing_status.notin_(_EXTRACTED_STATUSES | {"failed"})
    )
    failed = library.filter(Document.processing_status == "failed")
    query = library
    if search.strip():
        query = query.filter(LibraryEntry.title.icontains(search.strip(), autoescape=True))
    if format:
        query = query.filter(Document.format == format)
    if shelf == "reading":
        query = query.filter(LibraryEntry.last_read_at.isnot(None))
    elif shelf == "unread":
        query = query.filter(LibraryEntry.last_read_at.is_(None))
    elif shelf == "processing":
        query = query.filter(
            Document.processing_status.notin_(_EXTRACTED_STATUSES | {"failed"})
        )
    elif shelf == "failed":
        query = query.filter(Document.processing_status == "failed")
    total = query.count()
    if sort == "title":
        query = query.order_by(func.lower(LibraryEntry.title), Document.id)
    elif sort == "recent":
        query = query.order_by(
            func.coalesce(LibraryEntry.last_read_at, LibraryEntry.added_at).desc(), Document.id.desc()
        )
    else:
        query = query.order_by(LibraryEntry.added_at.desc(), Document.id.desc())
    # Use the same base scope for aggregate format counts, including notebook
    # and unfiled filters, while leaving search and shelf selectors out of it.
    format_query = (
        db.query(Document.format, func.count(Document.id))
        .join(LibraryEntry, LibraryEntry.document_id == Document.id)
        .filter(LibraryEntry.user_id == user.id)
    )
    if notebook_id is not None:
        format_query = format_query.filter(LibraryEntry.notebook_id == notebook_id)
    elif unfiled:
        format_query = format_query.filter(LibraryEntry.notebook_id.is_(None))
    formats = format_query.group_by(Document.format).all()
    return {
        "documents": _serialize_documents(db, query.offset(offset).limit(limit).all(), user.id),
        "total": total,
        "library_total": library.count(),
        "reading_count": reading.count(),
        "processing_count": processing.count(),
        "failed_count": failed.count(),
        "formats": dict(formats),
        "recent": _serialize_documents(db, reading.order_by(LibraryEntry.last_read_at.desc(), Document.id.desc()).limit(3).all(), user.id),
    }


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    limit: int = Query(default=50, ge=1, le=200),  # 50 covers a full page; 200 is the hard cap
    offset: int = Query(default=0, ge=0),
    notebook_id: Optional[int] = Query(default=None, ge=1),
    unfiled: bool = False,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """List owned documents, optionally scoped to a notebook."""
    query = db.query(Document).join(LibraryEntry, LibraryEntry.document_id == Document.id).filter(LibraryEntry.user_id == user.id)
    if notebook_id is not None and unfiled:
        raise HTTPException(status_code=422, detail="Choose a notebook or Unfiled")
    if notebook_id is not None:
        if not db.query(Notebook.id).filter(Notebook.id == notebook_id, Notebook.user_id == user.id).first():
            raise HTTPException(status_code=404, detail="Notebook not found")
        query = query.filter(LibraryEntry.notebook_id == notebook_id)
    elif unfiled:
        query = query.filter(LibraryEntry.notebook_id.is_(None))
    docs = query.order_by(LibraryEntry.added_at.desc()).offset(offset).limit(limit).all()
    return _serialize_documents(db, docs, user.id)


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    layout_mode: str = Form("auto"),
    notebook_id: Optional[int] = Form(None, ge=1),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Upload a document file for processing.

    layout_mode: auto | single_column | research_paper
    """
    import asyncio

    if notebook_id is not None and not db.query(Notebook.id).filter(
        Notebook.id == notebook_id,
        Notebook.user_id == user.id,
    ).first():
        raise HTTPException(status_code=404, detail="Notebook not found")

    file_ext = os.path.splitext(file.filename)[1].lower()

    if file_ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {file_ext}. Allowed: {ALLOWED_FORMATS}",
        )

    fmt = file_ext.lstrip(".")
    uploads_dir = os.path.join(settings.data_dir, "uploads", str(user.id))
    os.makedirs(uploads_dir, exist_ok=True)

    stored_name = _stored_filename(file.filename)
    file_path = os.path.join(uploads_dir, stored_name)

    # Stream the body to disk in bounded chunks instead of reading it whole
    # into RAM first. The old code buffered the entire upload (up to the cap
    # or more) in memory before the size check, so a multi-GB body was fully
    # materialised before the rejection - the check only ran after the cost.
    # Streaming makes memory bounded to one chunk and rejects the instant
    # the cap is crossed.
    max_bytes = settings.upload_max_size_mb * 1024 * 1024
    file_size = await asyncio.to_thread(_read_upload_capped, file, file_path, max_bytes)
    if file_size > max_bytes:
        os.unlink(file_path)
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {settings.upload_max_size_mb} MB)",
        )

    doc, created = await asyncio.to_thread(
        _save_uploaded_asset, db, user, file.filename, fmt, file_path, file_size,
        notebook_id, layout_mode if fmt == "pdf" else "single_column",
    )
    if not created:
        return document_response(doc, get_library_entry(db, user.id, doc.id))

    # Dispatch parse task only - summary tasks are chained from within the parse task
    try:
        _dispatch_parse(fmt, file_path, file.filename, doc.id)
    except Exception as e:
        # Broker unreachable or similar - don't leave the row silently
        # stuck at "pending" forever with no indication anything went
        # wrong; the file and row still exist so the user can retry
        # (re-parse) once whatever is broken is fixed.
        logger.exception("Failed to dispatch parse task for document %s", doc.id)
        doc.processing_status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Upload saved but processing failed to start: {e}")

    return document_response(doc, get_library_entry(db, user.id, doc.id))


# Magic-byte sniffing for the formats this endpoint accepts. A URL ending in
# ".pdf" is not proof the server actually returned a PDF - the single most
# common real-world failure here is a paywalled/institutional-access link
# returning a login page or "access denied" HTML body at 200 OK, which
# would otherwise sail through as a "successful" download and only fail
# later, opaquely, inside the parse task. EPUB/DOCX/PPTX are all zip
# containers (PK\x03\x04); a real content check for those would need to
# look inside the zip, which is more than this guard needs to do - it only
# needs to catch "this obviously isn't the claimed format", not fully
# validate structure (the parse task already does that and reports a real
# error if the zip is malformed).
_MAGIC_BYTES = {
    "pdf": (b"%PDF-",),
    "epub": (b"PK\x03\x04",),
    "docx": (b"PK\x03\x04",),
    "pptx": (b"PK\x03\x04",),
}


def _looks_like_format(content: bytes, fmt: str) -> bool:
    magic = _MAGIC_BYTES.get(fmt)
    if not magic:
        return True  # txt/md have no reliable magic bytes - nothing to check
    return any(content.startswith(m) for m in magic)


class _PinnedIPNetworkBackend(httpcore.SyncBackend):
    """httpcore network backend that connects only to pre-validated IPs.

    httpx normally resolves DNS at connect time, so a host that passes the
    private-range check in _validate_download_url can re-resolve to an
    internal address by the time the connection opens (0-TTL DNS rebinding)
    - and this endpoint then hands the fetched bytes to the parse pipeline
    as if the user had uploaded them. The pinned transport removes that
    window: the IPs are resolved and checked exactly once, and every TCP
    connect goes to one of those literal addresses. TLS still verifies the
    original hostname (SNI/cert use the real name), so pinning does not
    weaken certificate validation.
    """

    def __init__(self, pins: dict[str, tuple[str, ...]]):
        # Maps "host:port" -> the validated addresses. IPv6 pins carry
        # brackets, so the key is built with the same formatting.
        self._pins = pins
        self._default = httpcore.SyncBackend()

    def _pinned(self, host: str, port: int) -> tuple[str, ...] | None:
        normalized_host = host.removeprefix("[").removesuffix("]").lower().rstrip(".")
        if ":" in normalized_host:
            key = f"[{normalized_host}]:{port}"
        else:
            key = f"{normalized_host}:{port}"
        return self._pins.get(key)

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        addresses = self._pinned(host, port)
        if addresses is None:
            # Nothing pins this host: refuse rather than fall back to the
            # real resolver. That is what makes this a pin, not a hint.
            raise httpcore.ConnectError(
                f"Refusing to connect to {host}:{port}: not in the pinned set"
            )
        last_err: Exception | None = None
        for address in addresses:
            try:
                return self._default.connect_tcp(
                    address, port, timeout=timeout,
                    local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                # A rebinding victim that answers on one address but not
                # another must not get a free pass: try the rest, fail if
                # none work.
                last_err = e
        assert last_err is not None
        raise last_err

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        # URL uploads are http(s) only; unix sockets can never be pinned.
        raise httpcore.ConnectError("unix socket connections are not allowed")


def _pinned_client(
    pins: dict[str, tuple[str, ...]],
    response_hook=None,
) -> httpx.Client:
    """httpx.Client whose transport connects only to the validated IPs.

    Wraps a single-connection pool around the pinning backend. Pool max of
    1 means keepalive can never route a later request onto a connection that
    was opened for a different host. ``response_hook`` is run on every
    response (used to validate + pin redirect targets); it shares the SAME
    ``pins`` dict the backend reads, so pins added mid-stream take effect for
    the next connect.
    """
    pool = httpcore.ConnectionPool(
        max_connections=1, max_keepalive_connections=1,
        network_backend=_PinnedIPNetworkBackend(pins),
    )

    class _PinnedTransport(httpx.BaseTransport):
        def __init__(self, connection_pool: httpcore.ConnectionPool):
            self._pool = connection_pool

        def handle_request(self, request):
            # Same httpx->httpcore conversion httpx's own HTTPTransport does.
            from httpx._transports.default import (
                ResponseStream,
                map_httpcore_exceptions,
            )
            req = httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
            with map_httpcore_exceptions():
                resp = self._pool.handle_request(req)
            return httpx.Response(
                status_code=resp.status,
                headers=resp.headers,
                stream=ResponseStream(resp.stream),
                extensions=resp.extensions,
            )

        def close(self):
            self._pool.close()

    kwargs = {"transport": _PinnedTransport(pool)}
    if response_hook is not None:
        kwargs["event_hooks"] = {"response": [response_hook]}
    return httpx.Client(**kwargs)


# SSRF guards for /upload-url. A user-supplied URL must resolve to a real
# external host, otherwise the API container becomes an anchor for hitting
# internal services (Redis on :6379, Postgres, cloud metadata endpoints).
# Self-hosted setups that legitimately upload from a LAN/local server can
# opt in via DOWNLOAD_ALLOW_PRIVATE_HOSTS=true; metadata/multicast/reserved
# ranges stay blocked either way.
def _validate_download_url(url: str) -> dict[str, tuple[str, ...]]:
    """Validate a user-supplied download URL and return its pinned IPs.

    Raises HTTPException(400) on any scheme/host/private-range failure. On
    success returns a map "host:port" -> (ip, ...) of every resolved
    address, for the pinned transport to connect to: the fetch then talks to
    exactly these addresses and no DNS lookup happens again, closing the
    rebinding window between this check and the connect.
    """
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail="Only http(s) URLs can be uploaded from"
        )
    host = parsed.host
    if not host:
        raise HTTPException(status_code=400, detail="This URL has no hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise HTTPException(
            status_code=400, detail="This host could not be resolved"
        )
    pins: dict[str, tuple[str, ...]] = {}
    key = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(
                status_code=400,
                detail="This host points at a blocked internal address range",
            )
        if not settings.download_allow_private_hosts and (
            ip.is_private or ip.is_loopback
        ):
            raise HTTPException(
                status_code=400,
                detail="This host resolves to a private/loopback address, which "
                "is blocked for URL uploads. Set DOWNLOAD_ALLOW_PRIVATE_HOSTS=true "
                "if you upload from a server on your own network.",
            )
        pins.setdefault(key, []).append(str(ip))
    return {key: tuple(pins[key])}


@router.post("/upload-url")
async def upload_from_url(
    url: str = Form(...),
    notebook_id: Optional[int] = Form(None, ge=1),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Download a document from a URL and add it for processing."""
    if notebook_id is not None and not db.query(Notebook.id).filter(
        Notebook.id == notebook_id,
        Notebook.user_id == user.id,
    ).first():
        raise HTTPException(status_code=404, detail="Notebook not found")

    # Validate the initial URL (scheme + resolved IP) before touching the
    # network. The returned pins are the ONLY addresses the fetch will
    # connect to: no second DNS lookup ever happens, so a 0-TTL rebinding
    # flip between this check and the connect is impossible.
    pins: dict[str, tuple[str, ...]] = _validate_download_url(url)

    def _download(file_path: str) -> tuple[httpx.Headers, int]:
        """Stream the file to disk with a hard size cap.

        Returns (response headers, bytes written).

        Manual redirect loop (follow_redirects=False) rather than letting
        httpx follow: every hop is re-validated and its IPs pinned BEFORE
        the connection opens. A public URL 302-ing at 127.0.0.1 is blocked
        here, and a hop to another public host only works if THAT host also
        passes validation.
        """
        cap_bytes = settings.upload_max_size_mb * 1024 * 1024
        req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "application/pdf,application/epub+zip,*/*",
        }
        # Pinned transport: every TCP connect goes to one of the validated
        # IPs. No DNS lookup at connect time means a rebinding flip cannot
        # redirect the connection to an internal address after the check.
        with _pinned_client(pins) as client:
            current = url
            for _ in range(10):  # hard cap on hop count
                with client.stream("GET", current, headers=req_headers, timeout=60) as resp:
                    if resp.is_redirect and resp.next_request is not None:
                        target = str(resp.next_request.url)
                        pins.update(_validate_download_url(target))
                        current = target
                        continue
                    resp.raise_for_status()
                    declared = resp.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > cap_bytes:
                        raise HTTPException(status_code=400, detail="File too large")
                    # Stream to disk in chunks; stop the instant the cap is
                    # crossed instead of buffering the whole body in RAM.
                    written = 0
                    with open(file_path, "wb") as dst:
                        for chunk in resp.iter_bytes():
                            written += len(chunk)
                            if written > cap_bytes:
                                raise HTTPException(
                                    status_code=400, detail="File too large"
                                )
                            dst.write(chunk)
                    return resp.headers, written
            raise HTTPException(status_code=400, detail="Too many redirects")

    # The format/extension is derived from the URL (falling back to the
    # response content-type), so the destination path is known BEFORE the
    # download starts: the body can stream straight to disk.
    safe_title = os.path.basename(url.split("?")[0]) or "download.bin"
    uploads_dir = os.path.join(settings.data_dir, "uploads", str(user.id))
    os.makedirs(uploads_dir, exist_ok=True)
    # Same collision/traversal concern as the direct-upload path: the
    # disk name is uuid-derived, the human-readable name lives only in
    # Document.title.
    file_path = os.path.join(uploads_dir, _stored_filename(safe_title))

    try:
        headers, file_size = await asyncio.to_thread(_download, file_path)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=400,
            detail=f"The server rejected this URL (HTTP {e.response.status_code}) - "
            "it may require login or institutional access.",
        )
    except Exception as e:
        # Log the full internal error, but never echo it to the client: it
        # can leak internal hostnames, ports, and stack details.
        logger.warning("URL download failed for %s: %s", url, e)
        raise HTTPException(
            status_code=400,
            detail="Failed to download this URL - the site may be unreachable "
            "or may not serve the file directly.",
        )

    file_ext = os.path.splitext(url.split("?")[0])[1].lower()
    if not file_ext:
        content_type = headers.get("content-type", "").split(";")[0].strip()
        ext_map = {
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "application/epub+zip": ".epub",
        }
        file_ext = ext_map.get(content_type, "")

    if file_ext not in ALLOWED_UPLOAD_EXTS:
        os.unlink(file_path)
        raise HTTPException(status_code=400, detail="Unsupported URL format")

    fmt = file_ext.lstrip(".")

    # Magic-byte check on the first few bytes of what actually landed on
    # disk: the extension came from the URL, not from the file.
    with open(file_path, "rb") as f:
        head = f.read(8)
    if not _looks_like_format(head, fmt):
        os.unlink(file_path)
        content_type = headers.get("content-type", "unknown")
        raise HTTPException(
            status_code=400,
            detail=f"This URL didn't return a {fmt.upper()} file (got {content_type}) - "
            "it may be a login page, paywall, or the link may need to be opened "
            "in a browser first to get a direct download URL.",
        )

    doc, created = await asyncio.to_thread(
        _save_uploaded_asset, db, user, safe_title, fmt, file_path, file_size,
        notebook_id, "auto", url,
    )
    if not created:
        return {"id": doc.id, "status": doc.processing_status}

    # Dispatch parse task only - summary tasks are chained from within the parse task
    try:
        _dispatch_parse(fmt, file_path, safe_title, doc.id)
    except Exception as e:
        logger.exception("Failed to dispatch parse task for document %s", doc.id)
        doc.processing_status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Upload saved but processing failed to start: {e}")

    return {"id": doc.id, "status": doc.processing_status}


class LayoutModeUpdate(BaseModel):
    """Request body for updating a document's layout mode and re-parsing."""
    layout_mode: str = Field(..., pattern="^(auto|single_column|research_paper)$")


@router.post("/{doc_id}/re-parse")
def reparse_document(
    doc_id: int,
    body: LayoutModeUpdate | None = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Re-parse an already-extracted PDF document.

    Optionally update the layout_mode before re-parsing. If the document was
    never successfully extracted (status != 'extracted'), it will be parsed
    with the existing layout_mode instead.

    After re-parsing, section detection re-runs automatically while any
    existing summaries are preserved.
    """
    doc = get_document_for_user(db, user.id, doc_id)

    doc = db.query(Document).filter(Document.id == doc_id).with_for_update().one()
    members = db.query(LibraryEntry.id).filter(LibraryEntry.document_id == doc_id).count()
    if doc.user_id != user.id or doc.is_shared or members > 1:
        raise HTTPException(status_code=409, detail="This document version is shared or reused. Upload a revised PDF to create a new version.")

    if doc.format != "pdf":
        raise HTTPException(
            status_code=400,
            detail=f"Re-parse only supports PDF documents, got {doc.format}",
        )

    if body:
        doc.layout_mode = body.layout_mode

    doc.processing_status = "re-parsing"
    db.commit()
    db.refresh(doc)

    reparse_pdf_document.delay(doc_id)
    return {"doc_id": doc_id, "status": "queued", "layout_mode": doc.layout_mode}


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get a single document by ID."""
    doc = get_document_for_user(db, user.id, doc_id)
    return document_response(doc, get_library_entry(db, user.id, doc_id))


@router.get("/{doc_id}/status")
def get_document_status(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get processing status for a document."""
    doc = get_document_for_user(db, user.id, doc_id)
    return {"id": doc.id, "processing_status": doc.processing_status, "num_pages": doc.num_pages}


@router.put("/{doc_id}/progress", response_model=DocumentResponse)
def update_reading_progress(
    doc_id: int,
    body: ReadingProgressIn,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Record where the reader is, so the document reopens there.

    Idempotent and cheap on purpose: the client sends this while reading, and
    the only thing it does is overwrite three columns on a row the user
    already owns. There is no history to append to - "where I was" has exactly
    one useful value, and keeping every intermediate scroll position would be
    a write per second per reader in exchange for nothing anyone would read.
    """
    doc = get_document_for_user(db, user.id, doc_id)

    # Clamp rather than reject: a page count can shrink under the reader when
    # a document is re-parsed with a different layout mode, and failing the
    # save would mean the position is silently never recorded again.
    page = body.page
    if doc.num_pages:
        page = max(1, min(page, doc.num_pages))

    entry = get_library_entry(db, user.id, doc_id)
    entry.last_read_page = page
    entry.last_read_position = body.position
    entry.last_read_at = datetime.now(timezone.utc)
    db.commit()
    return document_response(doc, entry)


@router.get("/{doc_id}/pages", response_model=list[ExtractedTextResponse])
def get_document_pages(
    doc_id: int,
    limit: int = Query(default=100, ge=1, le=500),  # pages are large payloads; higher default but capped
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get extracted pages for a document, paginated."""
    get_document_for_user(db, user.id, doc_id)

    pages = db.query(ExtractedText).filter(
        ExtractedText.document_id == doc_id
    ).order_by(ExtractedText.page_number).offset(offset).limit(limit).all()
    return pages


@router.get("/{doc_id}/page/{page_num}", response_model=ExtractedTextResponse)
def get_page(
    doc_id: int,
    page_num: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get a specific page's extracted text."""
    get_document_for_user(db, user.id, doc_id)

    page = db.query(ExtractedText).filter(
        ExtractedText.document_id == doc_id,
        ExtractedText.page_number == page_num
    ).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.get("/{doc_id}/summaries")
def get_document_summaries(
    doc_id: int,
    mode: Optional[str] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Get all summaries for a document."""
    doc = get_document_for_user(db, user.id, doc_id)

    if mode is None or mode == "section":
        ensure_section_stubs(db, doc, user.id)

    query = db.query(Summary).filter(
        Summary.document_id == doc_id,
        Summary.user_id == user.id,
        Summary.mode != "page_job",
    )
    if mode:
        query = query.filter(Summary.mode == mode)

    summaries = query.order_by(Summary.page_number, Summary.start_page).all()
    return summaries


@router.post("/{doc_id}/summaries/section")
def generate_section_summary(
    doc_id: int,
    body: SectionSummaryGenerateRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Generate (or return, if already generated) the summary for a single
    detected section. Lets the user pick just the chapter they want to read
    instead of waiting for whole-book background summarization.
    """
    doc = get_document_for_user(db, user.id, doc_id)

    ensure_section_stubs(db, doc, user.id)
    _lock_library_entry(db, user.id, doc_id)

    summary = db.query(Summary).filter(
        Summary.document_id == doc_id,
        Summary.user_id == user.id,
        Summary.mode == "section",
        Summary.section_title == body.section_title,
    ).first()
    if not summary:
        raise HTTPException(status_code=404, detail="Section not found")

    if summary.content:
        return summary

    if summary.status != "generating":
        from app.tasks.summary_tasks import generate_single_section_summary
        _enqueue_personal_summary(db, summary, generate_single_section_summary,
                                  doc_id, body.section_title, user.id)

    return summary


@router.post("/{doc_id}/summaries/page")
def generate_page_summary(
    doc_id: int,
    body: PageSummaryGenerateRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Generate (or return, if already generated) the summary for a single
    page. On-demand counterpart to the whole-book background job - lets the
    user read a page's guided-teaching explanation without waiting for the
    whole document to be summarized.
    """
    get_document_for_user(db, user.id, doc_id)
    _lock_library_entry(db, user.id, doc_id)

    page = db.query(ExtractedText).filter(
        ExtractedText.document_id == doc_id,
        ExtractedText.page_number == body.page_number,
    ).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    summary = db.query(Summary).filter(
        Summary.document_id == doc_id,
        Summary.user_id == user.id,
        Summary.mode == "page",
        Summary.page_number == body.page_number,
    ).first()
    if not summary:
        summary = Summary(
            document_id=doc_id,
            user_id=user.id,
            mode="page",
            page_number=body.page_number,
            content="",
        )
        db.add(summary)
        db.flush()

    if summary.content:
        return summary

    if summary.status != "generating":
        from app.tasks.summary_tasks import generate_single_page_summary
        _enqueue_personal_summary(db, summary, generate_single_page_summary,
                                  doc_id, body.page_number, user.id)

    return summary


@router.post("/{doc_id}/summaries/pages/all")
def generate_all_page_summaries(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Explicitly queue whole-book page summarization (the old default
    upload-time behavior). Can take hours for a large book - idempotent
    across retries, see tasks/summary_tasks.generate_page_summaries.
    """
    get_document_for_user(db, user.id, doc_id)
    doc = db.query(Document).filter(Document.id == doc_id).with_for_update().one()
    if doc.processing_status not in _EXTRACTED_STATUSES:
        raise HTTPException(status_code=409, detail="Document text is not ready for summaries")
    marker = db.query(Summary).filter(
        Summary.document_id == doc_id,
        Summary.user_id == user.id,
        Summary.mode == "page_job",
    ).first()
    if marker is not None and marker.status == "generating":
        return {"doc_id": doc_id, "status": "already_generating"}
    if marker is None:
        marker = Summary(
            document_id=doc_id,
            user_id=user.id,
            mode="page_job",
            page_number=0,
            content="",
        )
        db.add(marker)
    marker.status = "generating"
    db.commit()

    from app.tasks.summary_tasks import generate_page_summaries
    try:
        generate_page_summaries.delay(doc_id, user.id)
    except Exception:
        marker.status = "failed"
        db.commit()
        raise HTTPException(status_code=503, detail="Summary processing is temporarily unavailable") from None
    return {"doc_id": doc_id, "status": "queued"}


@router.delete("/{doc_id}")
def delete_document(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Remove the requester's library entry while retaining other users' book data."""
    from app.services.asset_lifecycle import retire_document_membership

    retire_document_membership(db, user_id=user.id, doc_id=doc_id)
    return {"detail": "Document removed from your library"}


@router.get("/{doc_id}/preview")
def get_document_preview(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Serve a cached asset cover after checking personal membership."""
    doc = get_document_for_user(db, user.id, doc_id)
    from app.services.document_preview import serve_document_preview
    return serve_document_preview(doc)


@router.get("/{doc_id}/file")
def download_file(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Download the original document file (for PDF.js viewer)."""
    doc = get_document_for_user(db, user.id, doc_id)
    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        doc.file_path,
        media_type="application/pdf",
        filename=get_library_entry(db, user.id, doc_id).title or "document.pdf",
    )
