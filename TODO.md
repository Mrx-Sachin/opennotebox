# OpenNoteBox - Build Progress

## Overview
AI-powered document reader. Upload PDFs, get AI summaries, chat with docs, listen with synced TTS highlighting.

**Status**: Active build - Phase 1 scaffolding complete  
**Last Updated**: 2026-07-12  
**Base Reference**: open-notebook (architecture, patterns), open-webui (UI reference)

---

## Tech Stack
- **Frontend**: Next.js 14 + TypeScript + Tailwind + Shadcn
- **Backend**: Python FastAPI + Celery + Redis
- **Database**: SQLite + SQLAlchemy
- **PDF Parsing**: OpenDataLoader PDF (OCR, bounding boxes)
- **AI**: Remote Ollama (Gemma 4 31B summary, Qwen 3.6 27B chat)
- **TTS**: Piper TTS (self-hosted)

---

## File Structure

```
open-notebox/
├── docker-compose.yml              ✅
├── .env.example                    ✅
├── backend/
│   ├── Dockerfile                  ✅
│   ├── pyproject.toml             ✅
│   └── app/
│       ├── main.py                ✅ FastAPI entry point, CORS, lifespan, routers
│       ├── config.py              ✅ Settings (Ollama, JWT, Celery, storage)
│       ├── database.py            ✅ SQLite, async/sync engines, session makers, init
│       ├── celery_app.py          ✅ Celery config, task imports, timeouts
│       ├── schemas.py             ✅ Pydantic models for all API I/O
│       ├── models/
│       │   └── __init__.py       ✅ User, Document, ExtractedText, Summary, Conversation, Message, TTSCache
│       ├── routers/
│       │   ├── auth.py           ✅ register, login, refresh, me, JWT middleware
│       │   ├── documents.py      ✅ upload file, upload URL, list, get, status, pages, summaries, delete, download file
│       │   ├── chat.py           ✅ create/list/delete conversations, send message with RAG
│       │   ├── tts.py            ✅ play summary, play page, get audio, check cache
│       │   ├── websocket.py      ✅ progress WebSocket, active connections
│       │   └── ollama.py         ✅ proxy to Ollama for model listing, chat
│       ├── services/
│       │   ├── auth_service.py   ✅ bcrypt hashing, JWT create/decode, user lookup
│       │   ├── pdf_parser.py     ✅ OpenDataLoader wrapper, page extraction, bounding boxes, pdfplumber fallback
│       │   ├── summarizer.py     ✅ page summaries, section summaries, batch parallel, section detection
│       │   ├── chat_engine.py    ✅ RAG Q&A with citations, context building, token budgeting
│       │   └── prompts.py        ✅ Jinja2 template loader, defaults
│       ├── tasks/
│       │   ├── pdf_tasks.py      ✅ parse pdf (Celery), parse txt, parse md
│       │   ├── summary_tasks.py  ✅ page summaries, section summaries, full pipeline
│       │   └── tts_tasks.py      ✅ generate TTS
│       └── middleware/
│           ├── __init__.py       ✅
│           └── auth_middleware.py ✅ JWT extraction, user injection
├── frontend/
│   ├── Dockerfile                 ✅
│   ├── package.json              ✅ Next.js 14
│   ├── next.config.js            ✅
│   ├── tsconfig.json             ✅
│   ├── tailwind.config.js        ✅ Dark mode colors, border radius
│   ├── postcss.config.js         ✅
│   └── src/
│       ├── app/
│       │   ├── layout.tsx         ✅ Root layout, providers, metadata
│       │   ├── page.tsx           ✅ Landing page with hero + feature cards + CTA
│       │   ├── globals.css        ✅ Tailwind, CSS vars, highlight classes
│       │   ├── providers.tsx      ✅ TanStack Query client provider
│       │   ├── login/page.tsx     ✅ Login form, token storage, redirect
│       │   ├── register/page.tsx  ✅ Registration form, validation, redirect
│       │   └── dashboard/page.tsx ✅ 3-panel layout, view toggle (summary/chat/PDF)
│       ├── components/
│       │   ├── layout/
│       │   │   ├── Sidebar.tsx     ✅ Logo, doc list, upload area
│       │   │   └── UploadArea.tsx  ✅ Drag-drop file, URL upload, progress indicator
│       │   ├── shared/
│       │   │   └── DocumentList.tsx ✅ Doc grid, status icons (pending/parsing/completed/failed), delete
│       │   ├── summary/
│       │   │   └── SummaryPanel.tsx ✅ Page/Section tabs, expandable summaries, word count, listen button
│       │   ├── chat/
│       │   │   ├── ChatPanel.tsx   ✅ Message list, input, send, new chat, citation links
│       │   │   └── CitationPopup.tsx ✅ Modal with sources, doc title, page, excerpt, jump button
│       │   ├── pdf/
│       │   │   ├── PdfViewer.tsx   ✅ Page navigation, zoom controls
│       │   │   └── HighlightOverlay.tsx ✅ Word-level highlight box
│       │   └── tts/
│       │       └── TtsPlayer.tsx   ✅ Play/pause/skip, speed control, progress
│       ├── hooks/
│       │   ├── useWebSocket.ts     ✅ Progress WebSocket for task updates
│       │   ├── useDocumentProgress.ts ✅ Status tracking (idle/parsing/summarizing/completed)
│       │   └── useTtsPlayer.ts     ✅ Audio player controls
│       └── services/
│           ├── api.ts             ✅ Axios client, auth interceptor
│           └── types.ts           ✅ TS interfaces (Document, Summary, Message, etc.)
├── tts/
│   ├── Dockerfile                 ✅
│   └── main.py                    ✅ Piper TTS HTTP wrapper with health, synthesis
└── prompts/
    ├── summary/page.jinja         ✅ Page summary prompt template
    ├── summary/section.jinja      ✅ Section summary prompt template
    ├── chat/qa.jinja              ✅ RAG Q&A chat prompt template
    └── extraction/structure.jinja ✅ Document structure extraction prompt
```

---

## Completed ✅

### Backend
- [x] Project structure + docker-compose + env config
- [x] FastAPI app with lifespan, CORS, health endpoint
- [x] SQLite database models (7 tables aligned with blueprint schema)
- [x] Pydantic schemas for all API I/O
- [x] JWT auth with bcrypt (register, login, refresh, me, token middleware)
- [x] Document upload (file + URL), list, get, status, delete, download
- [x] OpenDataLoader PDF parser service with fallback
- [x] Summarizer service (page + section, batch parallel, section detection)
- [x] RAG chat engine with citations, context budgeting, page scope
- [x] TTS service with Piper integration + cache
- [x] Celery tasks: PDF parse, page summaries, section summaries, TTS
- [x] WebSocket progress notifications
- [x] Ollama proxy router
- [x] Prompt templates (Jinja2)
- [x] Prompt loader service with defaults

### Frontend
- [x] Next.js setup with Tailwind + dark mode
- [x] Auth flow (login + register) with token storage
- [x] Landing page with hero + CTA
- [x] Dashboard with 3-panel layout (sidebar + center toggle + right TTS)
- [x] Document list with status indicators + auto-refresh
- [x] Upload area (file + URL)
- [x] Summary panel (page/section tabs, expandable, listen buttons)
- [x] Chat panel with messages, citations, citation popup
- [x] PDF viewer with page navigation + zoom
- [x] TTS player with play/pause/skip + speed control
- [x] WebSocket hook for task progress
- [x] API service with axios + auth interceptor
- [x] TypeScript interfaces

---

## Remaining 🚧

### Phase 1 - Core Functionality
- [ ] **Test backend end-to-end** - register -> upload PDF -> wait for processing -> view summaries
- [ ] **Real PDF.js integration** - current PdfViewer uses canvas render, needs full PDF.js with text layer
- [ ] **Word-level TTS synced highlighting** - bind word bounding boxes to audio timestamps
- [ ] **TTS service Docker with actual Piper model** - currently stub server
- [ ] **WebSocket progress from Celery tasks** - wire Celery task callbacks to WebSocket broadcast
- [ ] **Multi-document chat** - cross-document Q&A
- [x] **Source citations click-to-jump** - citation links scroll PDF viewer to page
- [x] **DOCX/PPTX parsing** - python-docx, python-pptx tasks

### Phase 2 - Enhancements
- [ ] **Word-level synced highlighting** - full Edge Read Aloud experience
- [ ] **Voice selection** - Piper voice model chooser
- [ ] **User preferences** - default speed, voice, theme
- [ ] **Summary export** - PDF, DOCX export
- [ ] **Bookmarking** - save positions in document
- [ ] **Annotations** - highlight + notes on PDF

### Phase 3 - Advanced
- [ ] **Document notebooks** - group multiple docs
- [ ] **Collaborative sharing** - share docs between users
- [ ] **Vector search** - semantic search across docs
- [ ] **EPUB support** - epublib parser
- [ ] **Audio/video support** - Whisper transcription
- [ ] **Mobile responsive** - adaptive layout

---

## Reused from open-notebook

| Component | Source | Adaptation |
|-----------|--------|------------|
| FastAPI router → service → domain pattern | open-notebook/api/ | Same structure, adapted for SQLite |
| LangGraph workflow pattern | open-notebook/graphs/ | Simplified to single-node for chat, kept async patterns |
| Multi-provider AI abstraction idea | open-notebook/ai/ | Simplified to Ollama-only (remote) for now |
| Prompt template system | open-notebook/prompts/ | Jinja2 templates, same loader pattern |
| Error classification utility | open-notebook/utils/ | Used in chat engine |
| Next.js + Zustand + TanStack Query | open-notebook/frontend/ | Same stack, adapted components |
| Shadcn UI + Tailwind dark mode | open-notebook/frontend/ | Same config structure |
| Async-first architecture | open-notebook/ | All operations non-blocking |
| Background job pattern | open-notebook/commands/ | Replaced surreal-commands → Celery |

---

## API Endpoints

### Auth
- `POST /api/auth/register` - Create account
- `POST /api/auth/login` - Get JWT tokens
- `POST /api/auth/refresh` - Refresh access token
- `GET /api/auth/me` - Get current user info

### Documents
- `GET /api/documents` - List user's documents
- `POST /api/documents/upload` - Upload file
- `POST /api/documents/upload-url` - Download from URL
- `GET /api/documents/{id}` - Get document info
- `GET /api/documents/{id}/status` - Processing status
- `GET /api/documents/{id}/pages` - All extracted pages
- `GET /api/documents/{id}/page/{num}` - Single page
- `GET /api/documents/{id}/summaries` - All summaries (option: ?mode=page|section)
- `GET /api/documents/{id}/file` - Download original file
- `DELETE /api/documents/{id}` - Delete document

### Chat
- `POST /api/chat/sessions` - New conversation
- `GET /api/chat/sessions` - List conversations
- `POST /api/chat/sessions/{id}/messages` - Send message
- `GET /api/chat/sessions/{id}/messages` - Get messages
- `PUT /api/chat/sessions/{id}/scope` - Update doc scope
- `DELETE /api/chat/sessions/{id}` - Delete conversation

### TTS
- `POST /api/tts/play/{summary_id}` - Play summary audio
- `POST /api/tts/play/page/{doc_id}/{page_num}` - Play raw page audio
- `GET /api/tts/audio/{cache_id}` - Stream cached audio
- `GET /api/tts/cache/{text_hash}` - Check cache status

### WebSocket
- `WS /ws/progress?token=<jwt>` - Real-time task progress

### Ollama Proxy
- `GET /api/ollama/models` - List models
- `POST /api/ollama/chat` - Proxy chat request

---

## Quick Start

```bash
# 1. Clone and setup
cd open-notebox
cp .env.example .env
# Edit .env with your Ollama URL + JWT secret

# 2. Docker (all services)
docker compose up -d

# 3. Or local dev
# Terminal 1: Redis
docker compose up -d redis

# Terminal 2: Backend
cd backend
pip install -e .
uvicorn app.main:app --port 8000

# Terminal 3: Celery worker
celery -A app.celery_app worker --loglevel=info

# Terminal 4: Frontend
cd ../frontend
npm install
npm run dev

# 4. Register a user
# Navigate to http://localhost:3000
```

---

## Notes
- Ollama must be running at the configured URL (default: http://ninja:11434/v1)
- JWT secret should be a random 32-char hex string
- Data stored in `./data/` directory (uploaded, extracted, summaries, TTS, SQLite DB)
- All file operations are user-isolated by user ID
