"""Headless Chrome route checker for the running app.

Same CDP bootstrap as verify_chrome.py but instead of (unviewable) screenshots
it asserts on the live DOM: that the auth guard did NOT kick us to /login, that
the expected top-level text/elements rendered, and that no Next.js error
overlay or React error-boundary is present. Prints a PASS/FAIL per check.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_chrome import CDP, http_json, wait, BASE, PORT  # noqa: E402


def evaluate(cdp, expression):
    r = cdp.cmd(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    res = r.get("result", {})
    if r.get("exceptionDetails"):
        return {"__error__": str(r["exceptionDetails"])}
    return res.get("value")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--refresh", required=True)
    args = ap.parse_args()

    profile = tempfile.mkdtemp(prefix="chrome-check-")
    chrome = subprocess.Popen(
        [
            "google-chrome", f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}", "--headless=new", "--no-sandbox",
            "--disable-gpu", "--ignore-certificate-errors",
            "--window-size=1440,900", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results = []
    cdp = None
    try:
        for _ in range(50):
            try:
                http_json("/json/version")
                break
            except Exception:
                wait(0.2)
        page = next(t for t in http_json("/json/list") if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Network.enable")
        cdp.cmd("Security.setIgnoreCertificateErrors", {"ignore": True})

        cdp.cmd("Page.navigate", {"url": f"{BASE}/login"})
        wait(3)
        cdp.cmd("Runtime.evaluate", {"expression":
            f"localStorage.setItem('token', {json.dumps(args.token)});"
            f"localStorage.setItem('refresh_token', {json.dumps(args.refresh)});'ok'"})

        # route -> list of substrings that must appear in body text
        expectations = {
            "/dashboard": ["Open NBX", "Add Document"],
            "/prompts": ["Prompt"],
            "/admin": ["Admin", "Users"],
        }
        for route, needles in expectations.items():
            cdp.cmd("Page.navigate", {"url": f"{BASE}{route}"})
            wait(4)
            path = evaluate(cdp, "location.pathname")
            body = evaluate(cdp, "document.body.innerText") or ""
            has_err_overlay = evaluate(
                cdp,
                "!!document.querySelector('nextjs-portal') || "
                "/error|something went wrong|application error/i.test("
                "(document.querySelector('nextjs-portal')||{}).textContent||'')",
            )
            # iframe error overlay renders in a shadow/portal too
            results.append((f"{route} stayed authenticated", path == route, f"landed on {path}"))
            results.append((f"{route} no error overlay", not has_err_overlay, str(has_err_overlay)))
            for n in needles:
                results.append((f"{route} shows {n!r}", n.lower() in body.lower(), f"len(body)={len(body)}"))
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()

    failures = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}   ({detail})")
        if not ok:
            failures += 1
    if cdp and cdp.console_errors:
        print("\nconsole errors:")
        for e in sorted(set(cdp.console_errors)):
            print("  ", e)
    if cdp and cdp.net_failures:
        print("\nnetwork failures:")
        for f in sorted(set(cdp.net_failures)):
            print("  ", f)
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILURES'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
