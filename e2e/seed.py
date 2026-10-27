#!/usr/bin/env python3
"""Idempotent seed for the dummy e2e account and its sample book.

Creates (or reuses) user e2e.dummy, uploads e2e-sample.pdf if the account
does not already have it, waits for parse, then plants two notes so the
viewer tests have a known highlight to paint.

    python3 e2e/seed.py

Env:
    E2E_API_URL     default http://127.0.0.1:3003
    E2E_USER        default e2e.dummy
    E2E_PASSWORD    default E2eDummy123!
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("E2E_API_URL", "http://127.0.0.1:3003").rstrip("/")
USER = os.environ.get("E2E_USER", "e2e.dummy")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2eDummy123!")
DOC_TITLE = "e2e-sample.pdf"
PDF_PATH = Path(__file__).resolve().parent / "fixtures" / "e2e-sample.pdf"

LONG_NOTE_MARKER = "e2e-long-highlight"
SHORT_NOTE_MARKER = "e2e-short-highlight"


def _req(method: str, path: str, *, token: str | None = None, data: bytes | None = None,
         content_type: str | None = None, timeout: int = 30) -> tuple[int, bytes]:
    url = f"{API}{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(method: str, path: str, body: dict | None = None, token: str | None = None):
    data = None
    ctype = None
    if body is not None:
        data = json.dumps(body).encode()
        ctype = "application/json"
    status, raw = _req(method, path, token=token, data=data, content_type=ctype)
    parsed = json.loads(raw.decode() or "null") if raw else None
    return status, parsed


def register_or_login() -> str:
    status, _ = _json("POST", "/api/auth/register", {"username": USER, "password": PASSWORD})
    if status not in (200, 400, 403):
        raise SystemExit(f"register failed: {status}")
    status, body = _json("POST", "/api/auth/login", {"username": USER, "password": PASSWORD})
    if status != 200 or not body or "access_token" not in body:
        raise SystemExit(f"login failed: {status} {body}")
    print(f"seed: logged in as {USER}")
    return body["access_token"]


def find_doc(token: str) -> dict | None:
    status, docs = _json("GET", "/api/documents", token=token)
    if status != 200 or not isinstance(docs, list):
        raise SystemExit(f"list documents failed: {status} {docs}")
    for d in docs:
        if d.get("title") == DOC_TITLE:
            return d
    return None


def upload_pdf(token: str) -> dict:
    if not PDF_PATH.is_file():
        raise SystemExit(f"missing fixture PDF: {PDF_PATH} (run fixtures/make_pdf.py)")
    pdf = PDF_PATH.read_bytes()
    boundary = "----e2eSeedBoundary"
    filename = DOC_TITLE
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/pdf\r\n\r\n",
        pdf,
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="layout_mode"\r\n\r\n',
        b"single_column\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    status, raw = _req(
        "POST",
        "/api/documents/upload",
        token=token,
        data=body,
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=60,
    )
    if status != 200:
        raise SystemExit(f"upload failed: {status} {raw.decode(errors='replace')}")
    doc = json.loads(raw.decode())
    print(f"seed: uploaded {DOC_TITLE} as document {doc['id']}")
    return doc


def wait_parsed(token: str, doc_id: int, timeout_s: int = 90) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        status, body = _json("GET", f"/api/documents/{doc_id}", token=token)
        if status != 200:
            raise SystemExit(f"get document failed: {status} {body}")
        last = body
        st = body.get("processing_status")
        if st == "completed":
            print(f"seed: document {doc_id} parsed ({body.get('num_pages')} pages)")
            return body
        if st == "failed":
            raise SystemExit(f"parse failed for document {doc_id}")
        time.sleep(1.5)
    raise SystemExit(f"parse still {last.get('processing_status') if last else '?'} after {timeout_s}s")


def seed_notes(token: str, doc_id: int) -> None:
    status, notes = _json("GET", f"/api/notes?document_id={doc_id}", token=token)
    if status != 200 or not isinstance(notes, list):
        raise SystemExit(f"list notes failed: {status} {notes}")
    tags = {t for n in notes for t in (n.get("tags") or [])}
    if LONG_NOTE_MARKER in tags and SHORT_NOTE_MARKER in tags:
        print(f"seed: notes already present ({len(notes)})")
        return

    status, pages = _json("GET", f"/api/documents/{doc_id}/pages", token=token)
    if status != 200 or not pages:
        raise SystemExit(f"pages not ready: {status} {pages}")
    page1 = next((p for p in pages if p.get("page_number") == 1), pages[0])
    content = (page1.get("content") or "").replace("\n", " ").strip()
    if not content:
        raise SystemExit("page 1 has no extracted text")

    words = content.split()
    short = " ".join(words[:8]) if len(words) >= 8 else content
    created = 0
    if SHORT_NOTE_MARKER not in tags:
        status, body = _json(
            "POST",
            "/api/notes",
            {
                "document_id": doc_id,
                "page_number": 1,
                "quote": short,
                "color": "green",
                "tags": [SHORT_NOTE_MARKER],
            },
            token=token,
        )
        if status not in (200, 201):
            raise SystemExit(f"short note failed: {status} {body}")
        created += 1
    if LONG_NOTE_MARKER not in tags:
        status, body = _json(
            "POST",
            "/api/notes",
            {
                "document_id": doc_id,
                "page_number": 1,
                "quote": content,
                "color": "indigo",
                "note": "e2e planted long highlight",
                "tags": [LONG_NOTE_MARKER],
            },
            token=token,
        )
        if status not in (200, 201):
            raise SystemExit(f"long note failed: {status} {body}")
        created += 1
    print(f"seed: planted {created} note(s) on document {doc_id}")


def main() -> None:
    token = register_or_login()
    doc = find_doc(token)
    if doc is None:
        doc = upload_pdf(token)
    else:
        print(f"seed: reusing document {doc['id']}")
    if doc.get("processing_status") != "completed":
        doc = wait_parsed(token, doc["id"])
    seed_notes(token, doc["id"])
    print(f"seed: ready  user={USER}  doc={doc['id']}  {API}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        print(f"seed: cannot reach {API}: {exc}", file=sys.stderr)
        print("Is the stack up?  docker compose up -d", file=sys.stderr)
        sys.exit(2)
