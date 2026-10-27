#!/usr/bin/env python3
"""Auto-detects which of the four supported CLI coding agents (claude, codex,
opencode, grok) are installed and already logged in on THIS machine, starts a
bridge.py instance for each one found - each on its own fixed port (see
bridge.py's DEFAULT_PORTS) - and keeps them running, restarting any that
dies, so OpenNotebox's Settings -> AI Model picker can just show them as
ready with nothing to configure per tool.

Must run on your own machine, not inside a container: it shells out to CLIs
authenticated on your host, and OpenNotebox's backend (which is dockerized)
reaches these ports over host.docker.internal, while your browser (which
also runs on this host) reaches them directly over 127.0.0.1 to auto-detect
that they're up.

Usage:
    python3 tools/cli-bridge/launcher.py
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bridge_auth import KEY_PATH, load_secret  # noqa: E402

TOOLS = {"claude": 8765, "codex": 8766, "opencode": 8767, "grok": 8768}
BRIDGE = Path(__file__).parent / "bridge.py"

POLL_SECONDS = 1.0
# Backoff between restarts. A bridge that dies because its port is taken, or
# because the CLI behind it lost its login, would otherwise respawn as fast
# as the OS can fork - so back off, but keep retrying forever at the cap:
# whatever is wrong is usually something the user fixes (log back in, free
# the port), and the bridge should come back on its own when they do.
FIRST_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
# A bridge that stayed up this long counts as healthy, so the next crash
# starts backing off from scratch instead of inheriting an old penalty.
STABLE_SECONDS = 60.0
# How long a bridge gets to exit on its own after SIGTERM before SIGKILL.
SHUTDOWN_GRACE_SECONDS = 5.0


class Supervised:
    """One bridge process, restarted with backoff whenever it exits."""

    def __init__(self, tool: str, port: int, env: dict):
        self.tool = tool
        self.port = port
        self.env = env
        self.proc: subprocess.Popen | None = None
        self.backoff = FIRST_BACKOFF_SECONDS
        self.restarts = 0
        self.started_at = 0.0
        self.retry_at = 0.0

    def spawn(self) -> None:
        self.proc = subprocess.Popen([sys.executable, str(BRIDGE)], env=self.env)
        self.started_at = time.monotonic()

    def check(self) -> None:
        """Poll once; restart the process if it has exited and its backoff
        has elapsed."""
        if self.proc is not None and self.proc.poll() is None:
            return  # still running

        now = time.monotonic()

        if self.proc is not None:
            code = self.proc.returncode
            uptime = now - self.started_at
            # Reset the penalty for a process that had been up a good while:
            # a one-off crash after an hour is not the same failure mode as
            # one that cannot survive its first second.
            if uptime >= STABLE_SECONDS:
                self.backoff = FIRST_BACKOFF_SECONDS
            self.proc = None
            self.restarts += 1
            self.retry_at = now + self.backoff
            print(
                f"[launcher] {self.tool}: bridge exited (code {code}) after "
                f"{uptime:.0f}s - restarting in {self.backoff:.0f}s "
                f"(restart #{self.restarts})",
                flush=True,
            )
            self.backoff = min(self.backoff * 2, MAX_BACKOFF_SECONDS)
            return

        if now >= self.retry_at:
            print(f"[launcher] {self.tool}: restarting bridge on port {self.port}", flush=True)
            self.spawn()

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def reap(self, deadline: float) -> None:
        if self.proc is None:
            return
        try:
            self.proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            print(f"[launcher] {self.tool}: did not stop, killing", flush=True)
            self.proc.kill()
            self.proc.wait()


def main() -> None:
    # Generate the shared secret once here rather than letting four bridges
    # race to create the same file. Every bridge then reads this one value,
    # so a single key works for all of them.
    secret = load_secret()

    supervised: list[Supervised] = []
    for tool, port in TOOLS.items():
        if shutil.which(tool) is None:
            print(f"[launcher] {tool}: not found on PATH, skipping")
            continue
        print(f"[launcher] {tool}: found, starting bridge on port {port}")
        env = {
            **os.environ,
            "CLI_BRIDGE_TOOL": tool,
            "CLI_BRIDGE_PORT": str(port),
            "CLI_BRIDGE_SECRET": secret,
        }
        entry = Supervised(tool, port, env)
        entry.spawn()
        supervised.append(entry)

    if not supervised:
        print(
            "[launcher] None of claude/codex/opencode/grok were found on PATH. "
            "Install and log into at least one, then re-run this script."
        )
        return

    print(f"[launcher] {len(supervised)} bridge(s) running, supervised. Ctrl+C to stop all.")
    print(f"[launcher] bridge key: {KEY_PATH} (Settings -> AI Model picks it up on Connect)")

    stopping = False

    def request_stop(signum, _frame) -> None:
        # Handle SIGTERM too, not just Ctrl+C: this gets run under nohup and
        # from process managers, and an unhandled SIGTERM would leave four
        # orphaned bridges still holding their ports.
        nonlocal stopping
        stopping = True
        print(f"\n[launcher] signal {signum} received, stopping...", flush=True)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    while not stopping:
        for entry in supervised:
            entry.check()
        # Sleep in one short slice so a signal is noticed promptly. Any
        # EINTR-style wakeup just means the loop re-checks `stopping`.
        time.sleep(POLL_SECONDS)

    for entry in supervised:
        entry.stop()
    deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
    for entry in supervised:
        entry.reap(deadline)
    print("[launcher] stopped.")


if __name__ == "__main__":
    main()
