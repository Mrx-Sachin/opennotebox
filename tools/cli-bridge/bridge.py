#!/usr/bin/env python3
"""Local OpenAI-compatible bridge in front of an already-authenticated CLI
coding agent (Claude Code, Codex, OpenCode, or Grok).

Why this exists: OpenNotebox's backend already treats "the active LLM" as
just {base_url, model, api_key} pointed at any OpenAI-compatible
/v1/chat/completions server (see backend/app/services/llm_settings.py,
chat_engine.py, summarizer.py - all langchain ChatOpenAI). None of these
four CLIs expose that protocol themselves, so this script is the smallest
possible adapter: it runs ON YOUR OWN MACHINE, next to a CLI you have
already logged into (`claude auth login` / `codex login` / `opencode auth
login` / `grok` welcome-screen auth), and translates one-shot chat
completions into one-shot CLI invocations. Your provider credentials never
leave your machine or touch OpenNotebox's server.

Usage:
    CLI_BRIDGE_TOOL=claude python3 bridge.py            # defaults: 127.0.0.1:8765
    CLI_BRIDGE_TOOL=codex CLI_BRIDGE_PORT=8765 python3 bridge.py
    CLI_BRIDGE_TOOL=opencode CLI_BRIDGE_MODEL=anthropic/claude-sonnet-5 python3 bridge.py
    CLI_BRIDGE_TOOL=grok python3 bridge.py

Then in OpenNotebox's Settings -> AI Model, add a backend:
    base_url: http://host.docker.internal:8765/v1   (self-hosting OpenNotebox
                                                       on this same machine)
    model:    a model id from /v1/models, or anything to use the CLI default
    api_key:  the bridge's shared secret (Settings fetches this for you when
              you click Connect; otherwise cat ~/.config/opennotebox/cli-bridge.key)

Requests must carry that secret as `Authorization: Bearer <key>`, which is
exactly how OpenNotebox's OpenAI client already sends a configured api_key.
Without it the bridge answers 401 - it listens on 0.0.0.0 by necessity (see
HOST below), so on any machine with a LAN or VPN address an unauthenticated
port would be an open door onto the user's paid CLI accounts.

If you're trying OpenNotebox on someone else's shared/hosted instance
instead of running it yourself, their server cannot reach 127.0.0.1 on your
machine - tunnel this port first (e.g. `cloudflared tunnel --url
http://localhost:8765` or `ngrok http 8765`) and use the tunnel's https URL
as base_url instead.

Deliberately not run in the CLI's "dangerously skip permissions" mode: each
CLI's own default tool-approval prompts still apply, which in a
non-interactive run means a tool call it isn't pre-approved for is denied
rather than executed - so a chat question can't turn into an unattended
file edit on your machine. Every invocation also runs inside a fresh empty
temp directory, never OpenNotebox's or your own project's working tree.
"""
import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge_auth import allowed_origins, load_secret, origin_allowed

# One fixed port per tool so running bridges for several CLIs side by side
# (see launcher.py) never collides even when CLI_BRIDGE_PORT is left unset.
DEFAULT_PORTS = {"claude": 8765, "codex": 8766, "opencode": 8767, "grok": 8768}

TOOL = os.environ.get("CLI_BRIDGE_TOOL", "claude")
MODEL = os.environ.get("CLI_BRIDGE_MODEL")  # optional; each CLI has its own default
PORT = int(os.environ.get("CLI_BRIDGE_PORT") or DEFAULT_PORTS.get(TOOL, 8765))
# 0.0.0.0, not 127.0.0.1: OpenNotebox's backend reaches this over
# host.docker.internal -> host-gateway, which arrives on the host's real
# network interface, not loopback - a bridge bound to 127.0.0.1 is
# unreachable from inside the container even though the hostname resolves.
# This does mean anything else on your LAN that can reach this host can also
# reach the bridge (no auth beyond an ignored api_key); it's still confined
# to shelling out to your already-authenticated CLI with its own default
# tool-approval prompts, never a bypassed/"skip permissions" mode.
HOST = os.environ.get("CLI_BRIDGE_HOST", "0.0.0.0")
TIMEOUT_SECONDS = float(os.environ.get("CLI_BRIDGE_TIMEOUT", "180"))

# Shared with every other bridge on this machine; see bridge_auth.py.
SECRET = load_secret()
ALLOWED_ORIGINS = allowed_origins()
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _flatten(messages: list[dict]) -> str:
    """These CLIs take one prompt string, not a chat message array - fold
    system/user/assistant turns into a single transcript the agent reads as
    context, ending on an explicit instruction to answer only the last
    user turn (otherwise it tends to keep chatting about earlier turns)."""
    lines = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):  # OpenAI content-parts form
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        lines.append(f"[{role}]\n{content}")
    lines.append("\nRespond only to the final [user] message above.")
    return "\n\n".join(lines)


# Linux caps a SINGLE argv string at MAX_ARG_STRLEN (32 pages = 128 KiB),
# regardless of the much larger total ARG_MAX. Passing the prompt as a
# command-line argument therefore dies with "[Errno 7] Argument list too
# long" the moment a document-grounded prompt carries real retrieved
# context - which is every real question, so only toy prompts survived.
# claude and codex can take the prompt on stdin, which has no such limit.
# opencode and grok have no stdin mode (opencode errors, grok reads "-"
# literally as the prompt), so oversized prompts are handed to them via a
# file in the scratch dir instead.
ARGV_SAFE_BYTES = 96 * 1024
PROMPT_FILENAME = "cli-bridge-prompt.txt"

_FILE_INSTRUCTION = (
    f"Read the file {PROMPT_FILENAME} in the current directory and respond to what it "
    "contains. Reply only with your answer to it."
)


# Placeholder meaning "whatever this CLI is already configured to use". It is
# deliberately not the tool's own name: OpenAI-compatible clients inspect the
# model id and change behaviour on it, and langchain routes anything matching
# its codex heuristic to the Responses API (POST /v1/responses), which this
# bridge does not implement - so a backend whose model was literally "codex"
# 404'd on every message. A neutral id no client has an opinion about avoids
# that entire class of bug.
DEFAULT_MODEL_ID = "cli-default"


def _resolve_model(requested: str | None) -> str | None:
    """Which model this request should run against.

    The OpenAI-shaped `model` field is how OpenNotebox's picker tells us
    which model to use, so it wins over CLI_BRIDGE_MODEL. Both DEFAULT_MODEL_ID
    and the tool's own name (what older presets stored) mean "unset" - let the
    CLI fall back to its own configured default.
    """
    requested = (requested or "").strip()
    if requested and requested.lower() not in (TOOL, DEFAULT_MODEL_ID):
        return requested
    return MODEL


def _build_command(
    prompt: str, scratch_dir: str, model: str | None
) -> tuple[list[str], str | None]:
    """Build the CLI invocation. Returns (argv, stdin_text); stdin_text is
    None when the prompt reaches the CLI some other way (argv or file)."""
    if TOOL == "claude":
        cmd = ["claude", "-p", "--output-format", "text"]
        if model:
            cmd += ["--model", model]
        return cmd, prompt  # no prompt arg: claude reads it from stdin
    if TOOL == "codex":
        cmd = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check"]
        if model:
            cmd += ["-c", f"model={model!r}"]
        cmd.append("-")  # "-" tells codex exec to read the prompt from stdin
        return cmd, prompt

    # opencode / grok: keep the proven argv path for ordinary prompts, and
    # fall back to a file only when argv physically cannot carry it.
    if len(prompt.encode()) <= ARGV_SAFE_BYTES:
        text = prompt
    else:
        with open(os.path.join(scratch_dir, PROMPT_FILENAME), "w") as fh:
            fh.write(prompt)
        text = _FILE_INSTRUCTION

    if TOOL == "opencode":
        cmd = ["opencode", "run"]
        if model:
            cmd += ["-m", model]
        cmd.append(text)
        return cmd, None
    if TOOL == "grok":
        cmd = ["grok", "-p", text]
        if model:
            cmd += ["-m", model]
        return cmd, None
    raise ValueError(f"Unknown CLI_BRIDGE_TOOL={TOOL!r}, expected claude/codex/opencode/grok")


# Model ids the Settings picker offers per tool. opencode and grok can list
# their real catalogue, so ask them. claude and codex have no list command,
# so fall back to the documented aliases each one accepts for --model.
_STATIC_MODELS = {
    "claude": ["opus", "sonnet", "haiku"],
    "codex": [],
}
_models_cache: dict = {"value": None, "expires": 0.0}


def _parse_model_lines(raw: str) -> list[str]:
    """Pull model ids out of a `<cli> models` dump.

    opencode prints one bare id per line, but grok wraps its list in prose
    ("You are logged in with ...", "Available models:", "- grok-4.5",
    "* h100-qwen3-6-27b (default)"). Model ids are single tokens, so
    anything with a space left in it is a sentence, not an id.
    """
    ids = []
    for line in raw.splitlines():
        s = line.strip()
        if s[:1] in ("-", "*"):
            s = s[1:].strip()
        s = s.replace("(default)", "").strip()
        if not s or " " in s or s.endswith(":"):
            continue
        ids.append(s)
    return ids


def _list_models() -> list[str]:
    """Model ids this CLI can run, cached for a minute (shelling out to the
    CLI on every Settings poll would be wasteful - the page polls every 4s)."""
    now = time.time()
    if _models_cache["value"] is not None and now < _models_cache["expires"]:
        return _models_cache["value"]

    ids: list[str] = []
    if TOOL in ("opencode", "grok"):
        try:
            out = subprocess.run(
                [TOOL, "models"], capture_output=True, text=True, timeout=30
            )
            if out.returncode == 0:
                ids = _parse_model_lines(out.stdout)
        except Exception:
            ids = []
    else:
        ids = list(_STATIC_MODELS.get(TOOL, []))

    # Always offer the CLI's own configured default as the first choice, so
    # "whatever I already set up" stays reachable without naming a model.
    first = MODEL or DEFAULT_MODEL_ID
    ids = [first] + [i for i in ids if i not in (first, TOOL)]
    _models_cache.update({"value": ids, "expires": now + 60})
    return ids


def _run_cli(prompt: str, model: str | None = None) -> str:
    with tempfile.TemporaryDirectory(prefix="cli-bridge-") as scratch_dir:
        cmd, stdin_text = _build_command(prompt, scratch_dir, model)
        result = subprocess.run(
            cmd,
            cwd=scratch_dir,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"{TOOL} exited {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    def _cors_origin(self) -> str | None:
        """Echo the caller's Origin only if it is OpenNotebox's own frontend.

        Never "*": a wildcard lets any page the user happens to have open
        read this bridge's responses from 127.0.0.1, which is both a way to
        lift the shared secret and a way to spend their CLI subscription.
        """
        origin = self.headers.get("Origin")
        return origin if origin_allowed(origin, ALLOWED_ORIGINS) else None

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        origin = self._cors_origin()
        if origin:
            # OpenNotebox's Settings page and model picker call this straight
            # from the browser (which runs on the host, not in the backend's
            # container) to detect live bridges and list their models.
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _is_loopback(self) -> bool:
        return self.client_address[0] in LOOPBACK

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        presented = token or self.headers.get("X-Bridge-Key", "")
        # Constant-time: this is a bearer token, and the comparison is
        # reachable by anyone who can route to this host.
        return bool(presented) and secrets.compare_digest(presented, SECRET)

    def do_OPTIONS(self) -> None:
        origin = self._cors_origin()
        self.send_response(204 if origin else 403)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            # Private Network Access: a page on a private address (the app at
            # https://192.168.x.x) requesting a loopback resource is a
            # private->local transition, which Chrome gates behind this
            # opt-in on the preflight. Without it, detecting a running bridge
            # fails from every address except localhost itself.
            if self.headers.get("Access-Control-Request-Private-Network") == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.rstrip("/")

        if path == "/v1/bridge-key":
            # How the frontend learns the secret so it can store it as the
            # backend's api_key. Loopback only, and only for OpenNotebox's
            # own origin - a caller that already has shell on this machine
            # could read the key file directly, so this grants nothing new,
            # but a LAN peer or a random website must never see it.
            if not self._is_loopback() or not self._cors_origin():
                self._json(403, {"error": "forbidden"})
                return
            self._json(200, {"key": SECRET})
            return

        if path == "/v1/models":
            # Readable unauthenticated from loopback so the Settings page can
            # detect a freshly-started bridge before it has the key. This
            # leaks nothing but a list of model names, and only to the
            # machine the bridge is already running on.
            if not (self._is_loopback() or self._authorized()):
                self._json(401, {"error": "missing or invalid bridge key"})
                return
            ids = _list_models()
            self._json(
                200,
                {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]},
            )
            return

        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        # No loopback exemption here: actually running the CLI is the
        # expensive, abusable operation, so it always costs a valid key.
        if not self._authorized():
            self._json(401, {"error": "missing or invalid bridge key"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            prompt = _flatten(body.get("messages", []))
            content = _run_cli(prompt, _resolve_model(body.get("model")))
        except Exception as e:
            self._json(500, {"error": {"message": str(e), "type": type(e).__name__}})
            return
        self._json(
            200,
            {
                "id": f"cli-bridge-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model") or MODEL or DEFAULT_MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )

    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        print(f"[cli-bridge] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[cli-bridge] tool={TOOL} model={MODEL or '(cli default)'} listening on http://{HOST}:{PORT}")
    server.serve_forever()
