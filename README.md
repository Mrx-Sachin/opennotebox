# open-notebox

A self-hosted, NotebookLM-style reader for PDFs and EPUBs: upload a document,
get AI summaries, chat with it over cited retrieval, generate a two-host
podcast or a set of spaced-repetition flashcards, and listen to any of it
with synced TTS narration - all running on your own infrastructure against
your own choice of LLM.

It exists for the same reason self-hosted alternatives to any SaaS product
exist: your documents and your questions about them stay on hardware you
control, talking to a model you chose, with no per-seat license and no
vendor able to shut it off. Lets do this

> **Status**: pre-1.0, single-deployment (not multi-tenant SaaS), used daily
> by its author. See [Known limitations](#known-limitations--roadmap)
> before relying on it for anything irreplaceable.

<!--
  Screenshot placeholder. Drop a real screenshot (dashboard with a document
  open, the chat panel, or the TTS bar) at docs/screenshot.png and swap the
  line below for:
  ![open-notebox dashboard](docs/screenshot.png)
-->
**[Screenshot placeholder - dashboard view with a document open, chat panel, and TTS bar]**

## Features

- **Multi-format reader** - PDF (with a real page-image + text-layer viewer,
  column-aware reading order, and a per-document layout mode for research
  papers), EPUB, DOCX, PPTX, TXT and Markdown, all normalized to the same
  page/section model.
- **AI summaries** - per-page and per-section/chapter summaries, generated
  on demand and cached, with automatic section/chapter detection (PDF
  outline, heading heuristics, or research-paper structure).
- **Chat with citations** - ask questions about one or more open documents;
  answers are grounded in retrieved passages (BM25 + embedding reranking)
  with numbered inline citations back to the source page, not a free-form
  hallucination.
- **TTS narration with sync** - page and section text read aloud via a
  self-hosted TTS engine (Piper or Kokoro-82M), with playback synced to
  on-page highlighting. Numbers, page furniture (page numbers, running
  headers/footers, tables) are filtered so narration doesn't read them
  aloud.
- **Podcasts** - turn a document or a section into a two-host spoken-dialogue
  podcast (distinct male/female voices), generated and cached as audio.
- **Flashcards** - AI-generated flashcards from document content, reviewed
  on an FSRS (Free Spaced Repetition Scheduler) schedule - the same
  algorithm behind modern Anki.
- **Bookmarks** - lightweight page/position markers per document.
- **Configurable LLM backend** - switch between Ollama and any other
  OpenAI-compatible endpoint (vLLM, llama.cpp server, a hosted API, etc.)
  from Settings in the UI, per-deployment, without editing config files.
- **Real-time progress** - a WebSocket pushes document-processing progress
  (parsing, summarizing) to the UI live instead of polling.

## Architecture

```
                     ┌─────────────┐
   browser  ──HTTPS▶ │   traefik   │  one origin, split by path, so the app
                     │  :443       │  is a secure context and never makes a
                     └──┬───────┬──┘  cross-origin request
              /api,/ws  │       │  everything else
                        │       ▼
                        │  ┌─────────────┐
                        │  │  frontend   │  Next.js 14 (App Router) + TS
                        │  │  :3000      │
                        │  └─────────────┘
                        ▼
                     ┌─────────────┐        ┌──────────────┐
                     │     api     │───────▶│  postgres    │
                     │  FastAPI    │        │  :5432       │
                     │  :8000      │        └──────────────┘
                     └──────┬──────┘
                            │ enqueues jobs
                            ▼
                     ┌─────────────┐
                     │   redis     │  Celery broker + result backend
                     │  :6379      │
                     └──────┬──────┘
                     ┌──────┴───────┬───────────────┐
                     ▼              ▼               ▼
              ┌───────────┐  ┌────────────┐  ┌────────────┐
              │  worker   │  │ worker-tts │  │    beat    │
              │ (parsing, │  │ (narration,│  │ (periodic: │
              │ summaries)│  │  podcasts) │  │ cache evict│
              └───────────┘  └──────┬─────┘  └────────────┘
                                    │ HTTP
                                    ▼
                             ┌─────────────┐
                             │     tts     │  Piper CLI + Kokoro-82M (ONNX),
                             │   :5111     │  optional CUDA
                             └─────────────┘

              api / worker  ───HTTP───▶  your LLM (Ollama / vLLM / etc.)
                                          not part of this stack
```

Batch work (document parsing, summaries, flashcard generation) and
latency-sensitive work (TTS narration a user is actively waiting on,
podcast synthesis) run on **separate Celery queues/workers** so a large
document re-parse can't starve narration behind it.

## Prerequisites

- Docker and Docker Compose v2 (`docker compose`, not the old
  `docker-compose`).
- An LLM you can reach over HTTP with an OpenAI- or Ollama-compatible API.
  This project does **not** ship a model or run inference itself - see
  [Pointing at an LLM backend](#pointing-at-an-llm-backend).
- Optional, for GPU-accelerated TTS: an NVIDIA GPU plus the NVIDIA Container
  Toolkit on the host - see [GPU acceleration for TTS](#gpu-acceleration-for-tts-optional).
  Without it, TTS runs on CPU automatically; it's slower but fully
  functional.

## Quickstart

```bash
git clone <this-repo-url> open-notebox
cd open-notebox

cp .env.example .env
# Generate a real secret and put it in .env as JWT_SECRET - the API refuses
# to start with the shipped placeholder.
openssl rand -hex 32

# Edit .env: at minimum, set JWT_SECRET, and point OLLAMA_BASE_URL /
# OLLAMA_MODEL_* at a real LLM endpoint (see below) if you're not running
# Ollama natively on this machine on the default port.

# TLS for the HTTPS entry point (see "Reaching it from another device").
./scripts/gen-certs.sh

docker compose up -d --build
```

Then open https://localhost, register an account (the first account created
isn't special - there's no separate admin bootstrap step), and upload a
document.

## Reaching it from another device

Traefik terminates HTTPS on port 443 and serves the frontend and the API from
one origin, so `https://<this-machine's-address>` works from a phone or a
second laptop with nothing to configure per device - any address in the
certificate does, including the LAN IP and a Tailscale address.

HTTPS is not cosmetic here. Browsers restrict a growing list of APIs to
"secure contexts" - HTTPS or `localhost` - including `crypto.randomUUID`,
`navigator.clipboard`, service workers and media capture. Served over plain
HTTP at a LAN address those APIs do not exist, which shows up as features
that work on the host machine and silently break everywhere else.

`scripts/gen-certs.sh` issues the certificate from a local CA it also
generates. Trust `certs/ca.crt` on each device you browse from to get a green
padlock:

- **Chrome/Chromium on Linux:**
  `sudo apt install -y libnss3-tools && certutil -d sql:$HOME/.pki/nssdb -A -t 'C,,' -n 'OpenNoteBox Local CA' -i certs/ca.crt`
- **Firefox:** Settings -> Privacy & Security -> Certificates -> View
  Certificates -> Authorities -> Import, and tick "identify websites".
- **iOS:** AirDrop/email `ca.crt`, install the profile, then enable it under
  Settings -> General -> About -> Certificate Trust Settings.
- **Android:** Settings -> Security -> Encryption & credentials -> Install a
  certificate -> CA certificate.

Skipping that gives a one-time click-through warning per device instead. The
connection is real HTTPS either way, so the secure-context APIs work
regardless - the warning is about identity, not encryption.

Tailscale works the same way: `https://100.x.y.z` or the MagicDNS name,
both of which `gen-certs.sh` puts in the certificate automatically.

The HTTPS port is published on IPv4 only (`HTTPS_BIND`, default `0.0.0.0`).
An unqualified port mapping makes Docker listen on both stacks, and on a host
with an ISP-assigned global IPv6 address that serves this app to the open
internet - which a host firewall does *not* catch, because Docker's published
ports are inserted ahead of it in the chain. LAN and Tailscale both work over
IPv4, so restricting it costs nothing.

Re-run `./scripts/gen-certs.sh` to add a new address (it reuses the existing
CA, so devices that already trust it stay trusted). Port 80 is deliberately
unused, so plain `http://` URLs are not served or redirected; use `https://`.

The `api` container runs the database migrations on startup (`RUN_MIGRATIONS=1`
in `docker-compose.yml` on that one container - see
`backend/docker-entrypoint.sh` for why only one container is allowed to);
`worker`, `worker-tts` and `beat` wait for the schema to be ready before
they start consuming jobs, so a slow first-boot migration won't crash them.

## Configuration reference

All variables live in `.env` (copy from `.env.example`); every one has a
working default in `docker-compose.yml` except `JWT_SECRET`.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `opennotebox` / `opennotebox` / `open_notebox` | Credentials for the bundled Postgres container. Bound to `127.0.0.1` only. |
| `POSTGRES_PORT` | `5432` | Host port for Postgres, if you want to connect a client directly. |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434/v1` | Ollama (or any OpenAI-compatible) endpoint. The default reaches an Ollama running natively on the Docker host. |
| `OLLAMA_MODEL_SUMMARY` | `gemma3:27b` | Model used for page/section/podcast summarization. |
| `OLLAMA_MODEL_CHAT` | `qwen2.5:32b` | Model used for the chat panel. |
| `VLLM_BASE_URL` / `VLLM_MODEL` / `VLLM_API_KEY` | empty | A second, optional OpenAI-compatible backend, selectable per-deployment from Settings in the UI. Leave blank to use only Ollama. |
| `JWT_SECRET` | **none - required** | Signs auth tokens. The API refuses to start on the shipped placeholder; generate one with `openssl rand -hex 32`. |
| `JWT_EXPIRY_HOURS` | `24` | Access token lifetime. |
| `UPLOAD_MAX_SIZE_MB` | `50` | Max upload size per document. |
| `TTS_CACHE_MAX_GIB` | `2` | On-disk cap for cached narration/podcast audio; a background job evicts least-recently-used files hourly above this. |
| `HTTPS_PORT` | `443` | Host port for the Traefik HTTPS entry point - the only one a browser needs. |
| `API_PORT` / `FRONTEND_PORT` | `8000` / `3000` | Host-side ports for `api` and `frontend`, bound to `127.0.0.1` for debugging. Browsers go through Traefik instead; hitting these directly will not work, because the frontend addresses the API relative to its own origin. |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | JSON array of allowed origins. Add yours if you're not using the default host/port. |
| `CORS_ALLOW_PRIVATE_NETWORKS` | `true` | Also allow any private-network origin (LAN/RFC1918, `.local`, Tailscale), so a phone or second laptop can use the app at `http://<this-machine-ip>:3003` without being listed above. Set `false` on a publicly reachable instance. |
| `TTS_MAX_CONCURRENCY` | auto | Concurrent TTS synthesis slots. Leave unset to let the service pick a sensible default based on whether a GPU is active. |

## Pointing at an LLM backend

open-notebox talks to any OpenAI-compatible chat-completions API - it has no
built-in inference. Two backends are supported out of the box, and you only
need one:

- **Ollama** (`OLLAMA_BASE_URL`) - the default. If Ollama runs on the same
  machine as Docker, `http://host.docker.internal:11434/v1` reaches it
  without changes (mapped explicitly for Linux in `docker-compose.yml`,
  where `host.docker.internal` isn't available by default). If it runs
  elsewhere, point this at that host, e.g. `http://192.168.1.50:11434/v1`.
  Pull whatever models you plan to use (`ollama pull gemma3:27b`, etc.) and
  set `OLLAMA_MODEL_SUMMARY` / `OLLAMA_MODEL_CHAT` to their tags.
- **vLLM, or any other OpenAI-compatible server** (`VLLM_BASE_URL`) - set
  this to add a second selectable backend (a bigger model on a GPU box on
  your LAN, a hosted OpenAI-compatible API, etc.). Once set, switch between
  backends from **Settings** in the UI without restarting anything.

Model choice matters a lot here: this was built and tuned against ~27-32B
class local models. Much smaller models will work but will produce weaker
summaries, worse retrieval reranking, and less reliable citations.

## GPU acceleration for TTS (optional)

The `tts` service (Piper + Kokoro-82M, both ONNX-based) runs on CPU by
default and works correctly that way - it's just slower per sentence. GPU
support is opt-in via a separate compose overlay
(`docker-compose.gpu.yml`) precisely so the base stack never fails to start
on a machine without an NVIDIA GPU.

If you have an NVIDIA GPU and want faster narration/podcast synthesis:

1. Confirm the driver is installed on the host: `nvidia-smi` should print
   your GPU.
2. Install the **NVIDIA Container Toolkit** on the Docker *host* (not in
   any container) - see [NVIDIA's install
   guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
   On Debian/Ubuntu this is roughly:
   ```bash
   sudo apt install nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker   # NB: restarts every running container
   ```
3. Start the stack with both compose files:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
   ```

**Why this is a separate file, and why it matters if you skip it:** a GPU
device reservation (`deploy.resources.reservations.devices`) doesn't
degrade gracefully - if requested and the toolkit isn't installed, Docker
refuses to start the *container asking for it*, full stop. Keeping it out
of the base `docker-compose.yml` means a plain `docker compose up` always
works, GPU or not; `tts/main.py` itself already falls back to CPU cleanly
whenever CUDA isn't available. Only reach for `docker-compose.gpu.yml` once
the toolkit is actually installed and `nvidia-smi` works.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for running the backend/frontend
outside Docker, tests, and linters.

## Troubleshooting

- **`docker compose -f docker-compose.yml -f docker-compose.gpu.yml up`
  fails, mentioning GPU/`nvidia` devices.** You don't have
  `nvidia-container-toolkit` installed (or don't have an NVIDIA GPU) - see
  [GPU acceleration](#gpu-acceleration-for-tts-optional). Plain
  `docker compose up` (without the `.gpu.yml` overlay) doesn't request a
  GPU at all and should work regardless.
- **API container keeps restarting / logs a JWT error.** `JWT_SECRET` is
  unset, empty, or still the placeholder. Generate one with `openssl rand
  -hex 32` and put it in `.env`.
- **Chat/summaries fail with a connection error to Ollama.** From inside
  the `api` container, `host.docker.internal` must resolve to your host.
  This is mapped for you on Linux in `docker-compose.yml`; confirm Ollama
  itself is listening on `0.0.0.0:11434` (not just `127.0.0.1`) if it's
  refusing connections from the container's IP.
- **Uploads fail with "File too large".** Raise `UPLOAD_MAX_SIZE_MB` in
  `.env` and restart the `api` and `worker` containers.
- **TTS is very slow.** Expected on CPU, especially with the Kokoro voices.
  Either use a Piper `medium`-tier voice (faster, more robotic) or set up
  GPU acceleration.
- **A document is stuck "processing".** Check `docker compose logs worker`
  - a stuck job usually means the LLM backend was unreachable or the
    document triggered a parser edge case. The `beat`/eviction jobs and the
    document-processing WebSocket push their own errors to the browser
    console as well.

## Known limitations / roadmap

This is an honest snapshot, not a promise:

- **Single-tenant-ish auth model.** Any account can register itself
  (`POST /api/auth/register` is open, by design, for self-hosting); there's
  no invite-only mode, admin role, or per-user resource quota yet. Don't
  expose this to the open internet without putting it behind your own
  auth/reverse proxy.
- **Tests are still thin.** Backend pytest covers API modules; a Playwright
  suite in `e2e/` walks login, the sample book, notes, and PDF links against
  a running stack (`./e2e/run.sh`). Most product surface is still untested.
  See [CONTRIBUTING.md](CONTRIBUTING.md).
- **English-only TTS voices** are bundled (Piper `en_US` voices, Kokoro's
  English voices). Other languages aren't wired up.
- **One shared LLM configuration per deployment**, not per-user - see
  `backend/app/routers/settings.py`.
- **No mobile-optimized layout** - the reader is built for desktop-width
  screens.

Contributions and issue reports on any of the above are welcome.

## License

[MIT](LICENSE)
