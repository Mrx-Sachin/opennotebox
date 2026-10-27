"""End-to-end coverage for forwarding a slow upstream event stream."""
from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_bridge_forwards_sse_events_before_upstream_finishes() -> None:
    events = [b"data: first\n\n", b"data: second\n\n", b"data: third\n\n"]

    class SlowUpstreamHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_error(404)

        def do_POST(self):
            body_size = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(body_size)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in events:
                self.wfile.write(event)
                self.wfile.flush()
                time.sleep(0.5)
            self.close_connection = True

        def log_message(self, format, *args):
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), SlowUpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    bridge_port = _free_port()
    bridge_path = Path(__file__).resolve().parents[2] / "tools" / "omniroute_bridge.py"
    environment = os.environ.copy()
    environment.update(
        {
            "BRIDGE_HOST": "127.0.0.1",
            "BRIDGE_PORT": str(bridge_port),
            "UPSTREAM_URL": f"http://127.0.0.1:{upstream.server_port}",
            "FALLBACK_UPSTREAM_URL": f"http://127.0.0.1:{upstream.server_port}",
        }
    )
    bridge = subprocess.Popen(
        [sys.executable, str(bridge_path)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                probe = http.client.HTTPConnection("127.0.0.1", bridge_port, timeout=0.2)
                probe.request("GET", "/health")
                response = probe.getresponse()
                response.read()
                probe.close()
                break
            except OSError:
                if bridge.poll() is not None:
                    raise AssertionError("OmniRoute bridge exited before becoming ready")
                time.sleep(0.05)
        else:
            raise AssertionError("OmniRoute bridge did not become ready")

        connection = http.client.HTTPConnection("127.0.0.1", bridge_port, timeout=4)
        started_at = time.monotonic()
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=b'{"stream":true}',
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        first_event = response.read1(1024)
        first_event_seconds = time.monotonic() - started_at
        remainder = response.read()
        connection.close()

        assert response.status == 200
        assert first_event == events[0]
        assert first_event_seconds < 0.75
        assert remainder == b"".join(events[1:])
    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=3)
        except subprocess.TimeoutExpired:
            bridge.kill()
            bridge.wait(timeout=3)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
