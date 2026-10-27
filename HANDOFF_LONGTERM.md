# Long-term performance/footprint work (not scheduled)

Deferred out of the 2026-08-08 senior-dev performance/footprint report.
These are bigger bets that need discussion or measurement before starting,
unlike the short-term items (frontend prod build, Celery per-child memory
cap, dead SQLite backup cleanup), which were small enough to just do.

## Status as of 2026-08-09

**The autonomous work queue (4c, then 4a, then 4d) is finished.** All three
shipped in commit `ba00161` and were re-verified against the running system
on 2026-08-09: an unauthenticated bridge call from the api container answers
401, two users on one "Claude Code" backend hold different models
(`{}` vs `{"Claude Code": "haiku"}`), and the launcher is supervising four
live bridges.

Everything still open below - items 1, 2, 3 and 4b - is blocked on
**measurement, not effort**, and deliberately so. Items 2 and 3 say in their
own text not to touch the code before there are numbers, and 4b says not to
bother until someone reports latency with figures attached. A future
autonomous run should NOT treat them as ready work.

The one genuinely ready, unblocked task is the prerequisite item 2 names but
does not itself perform: **per-stage latency instrumentation on the chat
path** (query rewrite / retrieval / rerank / generation). Nothing logs that
today, so items 2 and 3 cannot be evaluated at all. That is the honest next
piece of work, and it is an instrumentation task, not an optimization one.

## 1. TTS image's 3.63GB pip layer (onnxruntime-gpu + nvidia-*-cu12)

Mostly irreducible without a different base image strategy - a CUDA base
image is also multi-GB, so this isn't "fixable cheap." The real lever:
`tts/Dockerfile` bakes in six Piper voice models (~800MB) unconditionally
at build time, even though the deployment default voice is Kokoro. Piper
voices could download lazily on first actual selection instead, via the
same `download_missing_models` startup path that already exists for
Kokoro. Worth doing eventually; not urgent - it's image size, not runtime
cost.

## 2. Chat latency: rerank call frequency, retrieval candidate counts

We cut one of three sequential chat LLM calls (query-rewrite, skipped for
fresh/concrete questions - see `chat_engine.py::_should_skip_rewrite`).
The next lever is the same shape: does rerank need an LLM call as often as
it currently does (`chat_engine.py` TOP_K/CANDIDATES/SEMANTIC_K
constants), and could `_rerank`'s current "always call the LLM unless
candidate count <= TOP_K" threshold be raised. This needs actual
per-stage latency instrumentation before touching anything - don't guess,
measure first. No logging for per-stage latency exists yet; that's the
prerequisite, not the optimization itself.

## 3. Embeddings routing (Ollama-only, regardless of active chat backend)

`retrieval.py`'s `embed_texts()` is hardcoded to Ollama's native
`/api/embed` endpoint (`nomic-embed-text`), because the vLLM backend in
this deployment only serves chat models, not embeddings. So every
semantic search pays a round-trip to the Ollama host even when the user's
active chat backend is a different, remote vLLM server. An audit of
whether this round-trip is a meaningful fraction of total per-message
latency (vs. a small fixed cost next to LLM generation) was in flight via
a Grok subagent and did not complete - Grok's usage limit was hit
mid-task. Redo this audit (with Claude or Codex, not Grok, given the rate
limit) before deciding whether it's worth fixing. Do NOT change this
routing without that evidence - it may well be fine as-is.

## 4. Local CLI provider bridge (`tools/cli-bridge/`)

Added 2026-08-08/09 so a user can point OpenNotebox at a coding-agent CLI
they already pay for (Claude Code, Codex, OpenCode, Grok) instead of a
self-hosted GPU model. `launcher.py` auto-detects which CLIs are on PATH
and starts one `bridge.py` per tool on a fixed port (claude 8765, codex
8766, opencode 8767, grok 8768). Each bridge is a stdlib HTTP server
speaking OpenAI `/v1/chat/completions` + `/v1/models`, shelling out to the
CLI once per request in a fresh temp dir. Settings -> AI Model detects live
bridges from the browser over loopback and connects them in one click; the
in-chat picker (`frontend/src/components/chat/ModelPicker.tsx`) reads each
bridge's real catalogue and lets you pick a specific model.

Working and verified end to end. The open items below are structural, not
bugs to chase right now.

### 4a. Per-backend `model` in deployment-wide state - FIXED 2026-08-09

`app_settings.llm_backends` is shared by every user of a deployment;
`user_settings.prefs.active` is per-user. Picking a model in the chat
picker sets `active` (per-user, fine) *and* rewrites that backend's `model`
field (deployment-wide, not fine). Two users on one deployment who both
use the Claude bridge cannot hold different models - the second one's
choice silently changes the first one's. Single-user deployments, which is
every current install, never notice.

Reproduced through the real router code before fixing: with both users on
"Claude Code", user 1 picking `haiku` moved user 2 from `cli-default` to
`haiku` as well.

Fixed with the per-user override described here, not by moving the registry
into user settings - the registry being shared is deliberate (an operator
configures endpoints once).

- `prefs.model_overrides = {backendName: modelId}`, applied by
  `llm_settings.get_active_llm()` on top of the registry entry.
- `GET /api/settings` now returns both `model` (the shared registry value,
  what a client must round-trip when it PUTs `backends`) and
  `effective_model` (that user's override applied). Keeping them separate is
  what stops the bug coming back through the Settings modal, which PUTs the
  whole backend list back on every add/delete/connect.
- `PUT /api/settings` takes `llm.model_overrides` and MERGES it, so a client
  changes one backend without resending the others. An explicit null drops an
  override back to the registry model. Overrides for deleted backends are
  pruned, so removing and re-adding an endpoint cannot resurrect a stale
  model choice.
- `ChatPanel.selectModel` sends `model_overrides` instead of rewriting
  `backends`; `ModelPicker` marks a row selected against `effective_model`.

Verified end to end: two users on the same "Claude Code" backend resolved to
`haiku` and `sonnet` respectively and both answered a real chat, while the
registry entry stayed `cli-default`. Merge, null-clear and prune-on-delete
each checked separately.

Older `prefs` rows have no `model_overrides` key; every read is
`.get("model_overrides") or {}`, so no migration is needed.

### 4b. Cold process per request

Every message spawns a fresh CLI process - no session reuse, no streaming.
Cost is a fixed multi-second startup on top of generation, and the UI has
no token-by-token feedback on this path (chat is non-streaming app-wide, so
the bridge is not uniquely at fault). Conversation memory is unaffected:
OpenNotebox resends the last 6 messages from Postgres on every call
(`routers/chat.py`, `HISTORY_TURNS = 3`), so the CLI retaining nothing is
by design, not a gap. Persistent sessions would mean per-conversation CLI
processes and a lifecycle to manage; not worth it until someone complains
about latency with numbers attached.

### 4c. Bridge exposure and the ufw rule - DONE 2026-08-09

`bridge.py` binds `0.0.0.0`, not loopback, because the backend reaches it
via `host.docker.internal` -> host-gateway, which arrives on the host's
real interface. On a ufw host that also needs an explicit rule, scoped to
Docker's private range rather than an interface name (compose recreates
`br-<hash>` networks, so interface-scoped rules break):

    sudo ufw allow from 172.16.0.0/12 to any port 8765:8768 proto tcp

That exposure is now closed by a shared secret (`tools/cli-bridge/bridge_auth.py`),
generated on first run at `~/.config/opennotebox/cli-bridge.key`, mode 0600,
shared by all four bridges:

- `POST /v1/chat/completions` always requires `Authorization: Bearer <key>`.
  That is already how the OpenAI client sends a stored `api_key`, so the
  backend needed no auth code - only `chat_engine.py` mapping a 401 to a
  message that points at Settings instead of a raw SDK error.
- `GET /v1/models` is key-free from loopback only, so Settings can still
  detect a bridge before it holds the key.
- `GET /v1/bridge-key` hands the secret to a loopback caller whose `Origin`
  is OpenNotebox's frontend, which is what keeps Connect one click. CORS is
  no longer `*` - it echoes only an allowed origin - so a random site the
  user has open can neither read the key nor drive the bridge.

Verified from a non-loopback client (the api container): unauthenticated
POST 401, unauthenticated GET /v1/models 401, GET /v1/bridge-key 403, and
the legacy `not-needed` api_key 401. Authenticated calls to all four
bridges still list models and answer.

Remaining: `CLI_BRIDGE_ORIGIN` must be set if the frontend is not on
`localhost:3003`, otherwise Connect 403s. The ufw rule above is still
required - the secret is authorization, not network reachability.

### 4d. Smaller known edges

- `codex` has no `models` subcommand, so its catalogue is just its
  configured default. Revisit if Codex ships one.
- Placeholder model ids are `cli-default`, never the tool's own name. Fixed
  2026-08-09: OpenAI-compatible clients branch on the model id, and langchain
  routes anything matching its codex heuristic to the Responses API
  (`POST /v1/responses`), which the bridge does not implement - so the Codex
  preset, whose stored model was literally `codex`, 404'd on every message.
  Found while E2E-verifying 4c. Do not reintroduce tool-name model ids.
- Linux caps a single argv string at 128KiB (`MAX_ARG_STRLEN`), which any
  document-grounded prompt exceeds. claude/codex now take the prompt on
  stdin; opencode/grok have no stdin mode and get a file in the scratch
  dir past 96KiB. If either grows a stdin flag, prefer it - the file path
  depends on the CLI choosing to read the file it is told about.
- `launcher.py` supervision: DONE 2026-08-09. Reproduced first (SIGKILL a
  bridge; it stayed dead, its port refused connections, Settings dropped it
  to "Not running"). Each bridge is now restarted on exit with exponential
  backoff (1s doubling to a 30s cap), retrying at the cap forever rather
  than giving up - the usual causes (port taken, CLI login expired) are
  user-fixable and should self-heal. A bridge up longer than
  STABLE_SECONDS=60 resets its backoff, so an occasional crash after a long
  healthy run is not penalised like a crash loop. SIGTERM is now handled as
  well as SIGINT: the old launcher only caught KeyboardInterrupt, so
  `kill`ing it under nohup orphaned four bridges still holding their ports.
  Verified: restart after SIGKILL (~1s, port serving again), backoff
  sequence 1/2/4/8/16s under repeated crashes, reset to 1s after 87s
  uptime, and SIGTERM leaving zero orphans.

## What NOT to do (carried over from the original report, still holds)

- Not migrating off Celery/Redis - not the bottleneck, churn risk too high.
- Not rewriting retrieval into a vector DB - Postgres-native hybrid
  BM25+embeddings works fine at this scale (single-digit documents/user,
  55MB DB).
- Not chasing the CUDA layer size further than the lazy Piper-model
  download - diminishing returns, and "big but stable" beats "clever but
  fragile" for the one part of this stack doing local GPU inference.
