#!/usr/bin/env python3
"""OmniRoute Bridge for DeepRead / OpenNotebox.

Exposes an OpenAI-compatible endpoint that routes to the local model-router at
127.0.0.1:20130 (or OmniRoute at 127.0.0.1:20128). Direct runs bind to loopback
by default; Docker Compose explicitly sets BRIDGE_HOST for container access.

Allows Docker containers to reach Gemini 3.8 Flash (antigravity/gemini-3.8-flash-tiered)
and other models through the same route used by Claude Code, while respecting host
firewall rules on docker0 (ports 8765-8768 are permitted).
"""

from __future__ import annotations

import json
import ipaddress
import logging
import os
import socket
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Tuple
from urllib.parse import urlsplit

BIND_HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("BRIDGE_PORT", "8766"))
PRIMARY_UPSTREAM = os.environ.get("UPSTREAM_URL", "http://127.0.0.1:20130")
FALLBACK_UPSTREAM = os.environ.get("FALLBACK_UPSTREAM_URL", "http://127.0.0.1:20128")
ALLOWED_CLIENT_NETWORKS = tuple(
    ipaddress.ip_network(network.strip())
    for network in os.environ.get("BRIDGE_ALLOWED_CIDRS", "127.0.0.0/8,::1/128").split(",")
    if network.strip()
)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] omniroute-bridge: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("omniroute-bridge")


def split_origin(origin: str) -> Tuple[str, int]:
    u = urlsplit(origin)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port


def check_upstream(origin: str) -> bool:
    try:
        host, port = split_origin(origin)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        res = sock.connect_ex((host, port))
        sock.close()
        return res == 0
    except Exception:
        return False


def get_active_upstream() -> str:
    if check_upstream(PRIMARY_UPSTREAM):
        return PRIMARY_UPSTREAM
    if check_upstream(FALLBACK_UPSTREAM):
        return FALLBACK_UPSTREAM
    return PRIMARY_UPSTREAM


class BridgeHandler(BaseHTTPRequestHandler):
    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            address = None
        if address is None or not any(address in network for network in ALLOWED_CLIENT_NETWORKS):
            self.send_error(403, "Client network is not allowed")
            return False
        return True

    def _filter_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        for k, v in self.headers.items():
            if k.lower() not in HOP_BY_HOP:
                headers[k] = v
        return headers

    def _proxy(self, method: str) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        upstream_url = get_active_upstream()
        up_host, up_port = split_origin(upstream_url)

        headers = self._filter_headers()
        if body:
            headers["Content-Length"] = str(len(body))

        conn: HTTPConnection | None = None
        response_started = False
        try:
            conn = HTTPConnection(up_host, up_port, timeout=180.0)
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in HOP_BY_HOP:
                    self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True

            while True:
                # read() waits for the requested byte count or EOF. SSE events
                # are small and may arrive far apart, so that buffers the
                # stream until many events have accumulated. read1() forwards
                # the bytes currently available from the upstream socket.
                chunk = resp.read1(8192)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        except Exception as e:
            log.warning("Proxy error forwarding %s %s: %s", method, self.path, e)
            if not response_started and not self.wfile.closed:
                try:
                    err_msg = json.dumps({"error": {"message": f"Bridge error: {e}"}}).encode()
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_msg)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(err_msg)
                except Exception:
                    pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_HEAD(self) -> None:
        self._proxy("HEAD")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        # Suppress standard access logging to keep stdout clean
        pass


def main() -> int:
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), BridgeHandler)
    server.daemon_threads = True
    log.info("OmniRoute bridge running on http://%s:%s", BIND_HOST, BIND_PORT)
    log.info("Forwarding to primary: %s (fallback: %s)", PRIMARY_UPSTREAM, FALLBACK_UPSTREAM)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down bridge")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
