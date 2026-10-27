# CLI bridge

Lets OpenNotebox chat/summaries/flashcards/podcasts run against a coding
agent CLI you already have installed and logged into - Claude Code, Codex,
OpenCode, or Grok - instead of a self-hosted GPU model. No new dependency inside
OpenNotebox itself: the backend already treats "the active LLM" as any
OpenAI-compatible `{base_url, model, api_key}` (see
`backend/app/services/llm_settings.py`). This script is a ~150-line stdlib
adapter that speaks that protocol on one side and shells out to the CLI on
the other. Your provider login never leaves your machine.

## Run it

The easy way - auto-detects whichever of the four CLIs you have installed
and logged in, and starts a bridge for each one it finds, each on its own
fixed port (`claude` 8765, `codex` 8766, `opencode` 8767, `grok` 8768):

```bash
python3 launcher.py
```

It supervises them: a bridge that dies is restarted automatically, backing
off 1s, 2s, 4s... up to 30s so a bridge that cannot start (port already
taken, CLI login expired) does not respawn in a hot loop. It keeps retrying
at that cap rather than giving up, because the usual cause is something you
fix on your side and the bridge should come back on its own when you do. A
bridge that stays up a minute is treated as healthy, so its next crash backs
off from 1s again. Ctrl+C or `kill` stops every bridge - SIGTERM is handled,
so running it under `nohup` or a process manager will not orphan bridges
still holding their ports.

OpenNotebox's Settings -> AI Model page auto-detects which bridges are up
and lets you connect with one click - nothing to type in there. This is
exactly what that "Use a subscription you already have" section runs under
the hood.

To run just one CLI's bridge by hand instead (e.g. to pass a custom
`CLI_BRIDGE_MODEL`), pick the tool you're already authenticated with:

```bash
claude auth login      # or: codex login   /   opencode auth login   /   grok (first run)

CLI_BRIDGE_TOOL=claude python3 bridge.py
# CLI_BRIDGE_TOOL=codex    python3 bridge.py
# CLI_BRIDGE_TOOL=opencode CLI_BRIDGE_MODEL=opencode/big-pickle python3 bridge.py
# CLI_BRIDGE_TOOL=grok     python3 bridge.py
```

Each tool listens on its own default port (see above) unless
`CLI_BRIDGE_PORT` overrides it. `CLI_BRIDGE_MODEL` is optional for
`claude`/`codex`/`grok` (uses your CLI's own configured default); OpenCode has no
usable default here, so pass one - `opencode models` lists what your
account can use. (`grok models` lists yours too, if you want to override it.)

## Point OpenNotebox at it

If you used `launcher.py`, just open Settings -> AI Model and click Connect
on whichever CLI shows "Detected" - that's it.

To add one manually instead (e.g. you ran `bridge.py` directly), Settings ->
AI Model -> add a backend:

| field    | value |
|----------|-------|
| base_url | `http://host.docker.internal:<port>/v1` (self-hosting OpenNotebox on this same machine - the backend runs in a container, so `localhost` there is the container, not your host) |
| model    | a model id from `/v1/models`, or `cli-default` to use whatever the CLI is already configured with. Do NOT put the tool's own name here - see below |
| api_key  | the bridge's shared secret: `cat ~/.config/opennotebox/cli-bridge.key` |

If instead you're trying someone else's shared/hosted OpenNotebox rather
than running it yourself, their server cannot reach `127.0.0.1` on your
machine at all. Tunnel the port first and use the tunnel's https URL as
`base_url`:

```bash
cloudflared tunnel --url http://localhost:8765     # or: ngrok http 8765
```

## Authentication

The bridge binds `0.0.0.0`, not loopback - it has to, because OpenNotebox's
backend is dockerized and arrives over `host.docker.internal` on the host's
real interface. So anything that can route to your machine (LAN, VPN,
Tailscale) can reach the port, and without a key that would be an open door
onto the CLI subscriptions you pay for.

Every bridge on the machine therefore shares one secret, generated on first
run at `~/.config/opennotebox/cli-bridge.key` (mode 0600). Override it with
`CLI_BRIDGE_SECRET`, or move it with `CLI_BRIDGE_SECRET_FILE`.

- `POST /v1/chat/completions` always requires `Authorization: Bearer <key>`,
  which is exactly how OpenNotebox's OpenAI client sends a stored `api_key` -
  no bespoke auth code on the backend side.
- `GET /v1/models` is readable without a key from loopback only, so the
  Settings page can detect a bridge before it holds the key. From anywhere
  else it needs the key like everything else.
- `GET /v1/bridge-key` returns the secret, and only to a loopback caller
  whose `Origin` is OpenNotebox's own frontend. That is what lets Connect be
  one click. CORS is never `*`, so a random site you have open cannot read
  the key or drive the bridge.

If your frontend does not run on `localhost:3003`, set `CLI_BRIDGE_ORIGIN` to
its origin (comma-separated for more than one) or Connect will 403.

## Why the default model id is `cli-default`

OpenAI-compatible clients branch on the model id. langchain in particular
routes anything matching its codex heuristic to the Responses API
(`POST /v1/responses`), which this bridge does not implement - so a backend
whose model was literally `codex` 404'd on every message. The placeholder is
a neutral id no client has an opinion about. The bridge treats `cli-default`
(and, for older installs, the tool's own name) as "unset" and lets the CLI
use its own configured model.

## Safety notes

- Every request runs the CLI in a fresh empty temp directory, never
  OpenNotebox's or your own project's working tree.
- None of the "skip all permissions" flags are passed. Each CLI's own
  default tool-approval behavior still applies, so in this unattended
  context a tool call it isn't already cleared for is denied, not executed.
  Treat this as a Q&A backend, not a coding agent with your filesystem.
- Non-streaming only, one CLI process per request - fine for chat, not
  built for high concurrency.

## Smoke test

```bash
KEY=$(cat ~/.config/opennotebox/cli-bridge.key)

curl -s http://127.0.0.1:8765/v1/models                      # loopback: no key needed
curl -s http://127.0.0.1:8765/v1/chat/completions \
  -H "authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"Reply with exactly the word: pong"}]}'
```

Expect `"content": "pong"` in the response. Verified against real `claude`,
`codex`, `opencode`, and `grok` CLIs while writing this.
