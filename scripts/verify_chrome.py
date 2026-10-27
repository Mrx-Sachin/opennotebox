"""Drive headless Chrome via the DevTools Protocol (no Playwright needed).

Logs into the running app by seeding the tokens the SPA reads from
localStorage, then screenshots a few routes and reports JS console errors
plus failed network requests. Intended for eyeballing UI changes in CI-ish
"did anything blow up" checks.

Usage:
    python scripts/verify_chrome.py --token <jwt> --refresh <jwt>
"""
import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


BASE = os.environ.get("APP_BASE_URL", "https://localhost")
PORT = int(os.environ.get("APP_DEBUG_PORT", "9222"))


def http_json(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def send_frame(sock, data):
    payload = data.encode()
    header = bytearray([0x81])
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    header += mask
    sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("ws closed")
        buf += chunk
    return buf


def read_frame(sock):
    b0, b1 = recv_exact(sock, 2)
    ln = b1 & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", recv_exact(sock, 2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", recv_exact(sock, 8))[0]
    data = bytearray(recv_exact(sock, ln))
    fin = b0 & 0x80
    opcode = b0 & 0x0F
    return fin, opcode, data


def ws_connect(url):
    # url ws://127.0.0.1:PORT/devtools/page/ID
    path = url.split(f":{PORT}")[1]
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(1)
    return sock


class CDP:
    def __init__(self, page_ws):
        self.sock = ws_connect(page_ws)
        self.id = 0
        self.console_errors = []
        self.net_failures = []

    def _handle_event(self, msg):
        m = msg.get("method")
        p = msg.get("params", {})
        if m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "warning"):
            parts = [a.get("value", a.get("description", "")) for a in p.get("args", [])]
            self.console_errors.append(f"[{p.get('type')}] " + " ".join(map(str, parts)))
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            self.console_errors.append("[exception] " + d.get("text", "") + " " + str(d.get("exception", {}).get("description", "")))
        elif m == "Network.loadingFailed":
            self.net_failures.append(f"{p.get('errorText')} {p.get('type')}")
        elif m == "Network.responseReceived":
            r = p.get("response", {})
            if r.get("status", 0) >= 400:
                self.net_failures.append(f"HTTP {r.get('status')} {r.get('url')}")

    def cmd(self, method, params=None):
        self.id += 1
        send_frame(self.sock, json.dumps({"id": self.id, "method": method, "params": params or {}}))
        while True:
            fin, opcode, data = read_frame(self.sock)
            if opcode == 0x8:
                raise ConnectionError("peer closed")
            if opcode in (0x1, 0x2):
                msg = json.loads(bytes(data))
                if msg.get("method"):
                    self._handle_event(msg)
                if msg.get("id") == self.id:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg.get("result", {})



def wait(sec):
    time.sleep(sec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--refresh", required=True)
    ap.add_argument("--outdir", default="/tmp/chrome-shots")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    profile = tempfile.mkdtemp(prefix="chrome-verify-")
    chrome = subprocess.Popen(
        [
            "google-chrome",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--ignore-certificate-errors",
            "--allow-running-insecure-content",
            "--window-size=1440,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    console_errors, net_failures = [], []
    cdp = None
    try:
        for _ in range(50):
            try:
                http_json("/json/version")
                break
            except Exception:
                wait(0.2)
        pages = [t for t in http_json("/json/list") if t.get("type") == "page"]
        cdp = CDP(pages[0]["webSocketDebuggerUrl"])
        cdp.cmd("Page.enable")
        cdp.cmd("Network.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Security.setIgnoreCertificateErrors", {"ignore": True})

        # Seed tokens on the app origin, then visit each route.
        cdp.cmd("Page.navigate", {"url": f"{BASE}/login"})
        wait(3)
        cdp.cmd(
            "Runtime.evaluate",
            {
                "expression": (
                    "localStorage.setItem('token', %r);"
                    "localStorage.setItem('refresh_token', %r);"
                    "'ok'" % (args.token, args.refresh)
                )
            },
        )
        for route, name in [
            ("/dashboard", "dashboard"),
            ("/prompts", "prompts"),
            ("/admin", "admin"),
        ]:
            cdp.cmd("Page.navigate", {"url": f"{BASE}{route}"})
            wait(4)
            shot = cdp.cmd("Page.captureScreenshot", {"format": "png"})
            out = os.path.join(args.outdir, f"{name}.png")
            with open(out, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            # Report where we actually landed (auth redirects -> /login).
            here = cdp.cmd("Runtime.evaluate", {"expression": "location.pathname"})
            print(f"route {route:12s} -> {here.get('result', {}).get('value')}  saved {out}")
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()

    if cdp is not None:
        console_errors = cdp.console_errors
        net_failures = cdp.net_failures
    if net_failures:
        print("network failures:")
        for f in sorted(set(net_failures)):
            print("  ", f)
    if console_errors:
        print("console errors:")
        for e in sorted(set(console_errors)):
            print("  ", e)
    print("DONE")


if __name__ == "__main__":
    main()
