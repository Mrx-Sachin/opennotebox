# Project Status: OpenNoteBox Integration

**Last Updated**: 2026-07-12  
**Status**: Active / Ready for Verification  
**Workspace**: `/home/mrx/local/github/opennotebox/`

---

## 1. What Has Been Completed & Fixed

### Backend (FastAPI & Celery)
* **DOCX/PPTX Task Wiring**: Integrated task dispatch (`parse_docx.delay()`, `parse_pptx.delay()`) into both the file upload and URL download handlers in `backend/app/routers/documents.py`.
* **Celery Registration**: Added `app.tasks.doc_tasks` to the `imports` list in `backend/app/celery_app.py` so the Celery worker successfully registers and processes DOCX/PPTX tasks.
* **NameError Fix**: Resolved a Python startup crash in `backend/app/routers/auth.py` by defining `get_current_user_from_token` before its reference in the `get_me` route dependency.
* **Passlib / Bcrypt Version Mismatch**: Replaced `passlib.context.CryptContext` with direct calls to the `bcrypt` library in `backend/app/services/auth_service.py`. This resolves a well-known startup crash where `passlib` fails to read version strings from modern `bcrypt >= 4.0` packages.
* **MIME Magic Cleanup**: Removed unused `import magic` statements from `documents.py` to eliminate unnecessary external system dependency errors.
* **Pyproject Dependencies**: Substituted `open-data-loader-pdf` (which does not exist on PyPI) with `pdfplumber` in `pyproject.toml` to support the PDF extraction fallback chain out-of-the-box.

### Frontend (Next.js)
* **Relative Fetch Routing Fix**: Modified `src/app/register/page.tsx` and `src/app/login/page.tsx` to use the imported `register` and `login` helpers from `@/services/api` instead of raw `fetch("/api/...")` relative calls. This ensures client-side XHR requests correctly route to the backend port (`8001`) instead of the frontend dev server (`3003`).
* **Broken Page Directory Removal**: Deleted the residual, malformed directory `'frontend/src/app/dashboard/\[docId\]'` which had escaped brackets in the filesystem and caused Next.js build compilation failures.
* **Missing Directives**: Added the `"use client"` directive to `src/app/page.tsx`, `src/app/register/page.tsx`, and `src/app/login/page.tsx` to allow correct rendering of hooks (`useRouter`, `useState`, `useEffect`).
* **PDF.js Worker**: Added a `postinstall` script in `frontend/package.json` to copy `pdf.worker.min.mjs` from node modules to the `public/` directory for production-local worker loading, and updated `PdfViewer.tsx` to reference it locally.

### Infrastructure & Deployment
* **Port Conflict Resolution**: Mapped host ports in `docker-compose.yml` using `API_PORT` (mapped to `8001`) and `FRONTEND_PORT` (mapped to `3003`) to avoid conflicts with host-occupied ports (`8000` and `3000`).
* **Environment Configuration**: Created `.env` and modified `DATA_DIR` to use `./data` relative to the workspace for clean local isolation.

---

## 2. Current State of the Services

All Docker images have compiled and built cleanly:
* `open-notebox-redis-1` (Up/Healthy)
* `open-notebox-tts-1` (Up/Healthy - Piper model downloaded from HF successfully)
* `open-notebox-frontend-1` (Up/Healthy - Next.js dev server ready)
* `open-notebox-api-1` and `open-notebox-worker-1` are built with the new direct `bcrypt` password hashing library.

---

## 3. Next Steps (Handoff Instructions)

1. **Restart API and Worker**: Since the code was updated, spin up the new API and Worker container images:
   ```bash
   docker compose up -d api worker
   ```
2. **Verify Container Stability**: Run `docker compose ps` to ensure both containers are `Up` and not restarting.
3. **Register and Login**: 
   * Navigate to `http://localhost:3003/register`.
   * Create a test user.
   * Log in via `http://localhost:3003/login`.
   * Ensure you are successfully redirected to the dashboard.
4. **Test File Processing**: Upload a PDF, DOCX, or PPTX file to verify that the extraction tasks complete and load summaries onto the page.
