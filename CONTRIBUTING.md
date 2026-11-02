# Contributing to open-notebox

Thanks for considering it. This is a small, self-hosted project - the bar
for contributing is "make it better without making it more fragile," not
process for its own sake.

## Dev setup (without Docker)

You'll still need Postgres and Redis running somewhere reachable (Docker for
just those two is fine: `docker compose up -d postgres redis`); everything
else can run natively for a faster edit loop.

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp ../.env.example .env    # then edit: at minimum JWT_SECRET, and
                            # DATABASE_URL to point at your postgres,
                            # e.g. postgresql+psycopg://opennotebox:opennotebox@localhost:5432/open_notebox

alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Run a Celery worker in another terminal to process uploads/summaries/TTS:

```bash
celery -A app.celery_app worker -l info -c 4 -Q default,tts
```

### Frontend

```bash
cd frontend
npm install
cp ../.env.example .env.local   # only NEXT_PUBLIC_API_URL / NEXT_PUBLIC_WS_URL matter here
npm run dev
```

Point `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL` at wherever your backend
from the previous step is listening (`http://localhost:8000` /
`ws://localhost:8000` by default).

### TTS service

The `tts` service is simplest to keep running via Docker
(`docker compose up -d tts`) even during frontend/backend development - it
bundles several downloaded ONNX voice models that aren't worth reproducing
in a native venv. Point the backend's `tts_url` setting at it
(`http://localhost:5111` if you're running the backend natively against the
Dockerized `tts` container).

## Tests and linters

```bash
# Backend - from backend/, with the dev venv active
ruff check .
pytest

# Frontend - from frontend/
npx tsc --noEmit
npm run lint
npm run build

# Browser suite - stack must already be up (docker compose up -d)
./e2e/run.sh
```

The browser suite logs in as dummy user `e2e.dummy` / `E2eDummy123!` with a
tiny fixture book. Details: `e2e/README.md`. It is not part of GitHub
Actions yet because CI does not boot the full stack.

All four are wired up in CI (`.github/workflows/ci.yml`) and run on every
push and pull request. A PR that fails one of them needs a fix or a clear
explanation of why the failure is expected (e.g. it's exposing a pre-existing
issue outside the scope of your change - say so in the PR description
rather than silencing the check).

The backend test suite is thin right now (mostly one focused module:
`backend/test_pdf_column_order.py`, covering column-aware PDF reading
order). Adding tests - especially around the chat/RAG pipeline, document
parsing edge cases, and the Celery tasks - is one of the most valuable
things a contribution can do right now.

## Code style

- **Comments should explain *why*, not *what*.** The codebase leans heavily
  on this - a comment restating what the next line obviously does is worse
  than no comment; a comment explaining a non-obvious constraint, a bug it's
  working around, or a tradeoff it's making is exactly what to add. Look at
  any existing service or task module for the tone to match.
- **No em dashes ("—") anywhere** - in code comments, docs, or commit
  messages. Use a plain hyphen ("-") instead.
- Backend: formatted/linted with `ruff` (config in `backend/pyproject.toml`).
  Type hints on public function signatures are expected; the codebase is
  not strictly typed everywhere, but new code should trend that way, not
  away from it.
- Frontend: TypeScript strict mode is on (`tsconfig.json`); don't add
  `any` or `@ts-ignore` to route around a real type error - fix the type
  instead, or ask for review if the type really is unrepresentable cleanly.
- Match the existing formatting/structure of the file you're editing over
  introducing a new personal style.

## Commit messages

- One logical change per commit. Don't bundle an unrelated drive-by fix
  into a feature commit - split it out.
- Explain *why* the change was made, not just what changed - the diff
  already shows what changed.
- No em dashes (see above).
- Reference an issue number if one exists.

## Pull requests

1. Keep PRs scoped to one thing. A PR that touches the reader, the chat
   engine, and the TTS cache eviction logic is three PRs.
2. Make sure `ruff check`, `pytest`, `tsc --noEmit`, `npm run lint`, and
   `npm run build` all pass locally before opening the PR - CI runs the
   same checks and will block merge otherwise.
3. Describe what you tested manually, especially for anything touching
   document parsing, TTS, or the Celery task pipeline, where an automated
   test often doesn't exist yet.
4. If your change affects `docker-compose.yml`, `.env.example`, or any
   default configuration, call that out explicitly in the PR description -
   these affect every existing deployment on upgrade.

## Reporting bugs / security issues

Open a GitHub issue for ordinary bugs. For anything that looks like a
security issue (auth bypass, path traversal, secrets exposure), please
don't open a public issue - see the repository's security policy /
contact the maintainer directly instead.
