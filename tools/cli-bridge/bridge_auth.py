"""Shared secret for the CLI bridges, and the origin allowed to read it.

The bridge has to listen on 0.0.0.0 (OpenNotebox's backend is dockerized and
arrives over host.docker.internal -> host-gateway, on the host's real
interface, not loopback). That means anything which can route to this host -
LAN, VPN, Tailscale - can also reach the port, and before this module existed
that was enough to spend the user's CLI subscription.

The secret lives in one file shared by every bridge on the machine, so the
launcher can start four of them and a single key works for all four. It is
generated on first use, never transmitted except to a loopback caller from
the app's own origin, and doubles as the `api_key` OpenNotebox stores for the
backend - the OpenAI client already sends that as `Authorization: Bearer`,
so the backend needed no bespoke auth code at all.
"""
import os
import re
import secrets
from pathlib import Path

KEY_ENV = "CLI_BRIDGE_SECRET"
KEY_PATH = Path(
    os.environ.get("CLI_BRIDGE_SECRET_FILE")
    or Path.home() / ".config" / "opennotebox" / "cli-bridge.key"
)

# Origins allowed to read the key over loopback. Only OpenNotebox's own
# frontend qualifies: without this any random website the user visits could
# fetch the key from 127.0.0.1 in the background and then drive the bridge
# from anywhere. Override for a non-default frontend port/host.
DEFAULT_ORIGINS = (
    "http://localhost:3003",
    "http://127.0.0.1:3003",
)

# Behind Traefik the frontend has no fixed port and no single hostname - it is
# https://localhost, https://<lan-ip> or https://<tailscale-ip> depending on
# which device is being used - so an exact-match list cannot express it. This
# pattern accepts any origin whose host can only name a machine on the user's
# own network. It stays narrow in the way that matters: reading the key also
# requires the request to arrive from loopback, so an origin match alone gets
# a page nothing, and a public site can never match this pattern at all.
_LOCAL_HOST_RE = (
    r"localhost"
    r"|127(?:\.\d{1,3}){3}"
    r"|\[::1\]"
    r"|10(?:\.\d{1,3}){3}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"
    r"|169\.254(?:\.\d{1,3}){2}"
    r"|[A-Za-z0-9-]+\.(?:local|lan|home|internal)"
    r"|[A-Za-z0-9-]+"
)
LOCAL_ORIGIN_RE = re.compile(rf"https?://(?:{_LOCAL_HOST_RE})(?::\d+)?\Z")


def allowed_origins() -> set[str]:
    extra = os.environ.get("CLI_BRIDGE_ORIGIN", "")
    return {o.strip() for o in (*DEFAULT_ORIGINS, *extra.split(",")) if o.strip()}


def origin_allowed(origin: str | None, explicit: set[str]) -> bool:
    """True for an explicitly listed origin, or any private-network one."""
    if not origin:
        return False
    return origin in explicit or bool(LOCAL_ORIGIN_RE.match(origin))


def load_secret() -> str:
    """The shared secret, generating and persisting one on first use.

    0600 on both the file and its directory: this is a bearer token for the
    user's paid CLI accounts, and every bridge process on the machine reads
    the same path, so it must not be group/world readable.
    """
    from_env = os.environ.get(KEY_ENV, "").strip()
    if from_env:
        return from_env

    try:
        existing = KEY_PATH.read_text().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass

    secret = secrets.token_urlsafe(32)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create with the right mode from the start rather than chmod-ing after -
    # otherwise the secret is briefly world-readable on disk.
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secret + "\n")
    return secret
