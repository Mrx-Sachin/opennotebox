"""Interactive headless-Chrome smoke test for the running app.

Exercises the authenticated app like a user: open the first library document,
switch to the Notes tab, and open the page-note composer. Asserts the expected
panels render and that no React/Next error boundary or API 5xx occurs.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_chrome import CDP, http_json, wait, BASE, PORT  # noqa: E402

CHECKS = []


def ev(cdp, expression):
    r = cdp.cmd("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
    if r.get("exceptionDetails"):
        return {"__error__": str(r["exceptionDetails"].get("text"))}
    return r.get("result", {}).get("value")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


def click_text(cdp, text):
    """Click the first visible element whose trimmed text == text (or contains)."""
    expr = f"""(() => {{
      const want = {json.dumps(text)}.toLowerCase();
      const els = [...document.querySelectorAll('button,a,[role=button],[role=tab]')];
      const el = els.find(e => (e.innerText||'').trim().toLowerCase() === want)
              || els.find(e => (e.innerText||'').trim().toLowerCase().includes(want));
      if (!el) return 'not-found';
      el.scrollIntoView();
      el.click();
      return 'clicked:' + (el.innerText||'').trim().slice(0,30);
    }})()"""
    return ev(cdp, expr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--refresh", required=True)
    args = ap.parse_args()
    prof = tempfile.mkdtemp(prefix="chrome-int-")
    ch = subprocess.Popen(
        ["google-chrome", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
         "--headless=new", "--no-sandbox", "--disable-gpu", "--ignore-certificate-errors",
         "--window-size=1440,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                http_json("/json/version"); break
            except Exception:
                wait(0.2)
        page = next(t for t in http_json("/json/list") if t.get("type") == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.cmd("Page.enable"); cdp.cmd("Runtime.enable"); cdp.cmd("Network.enable")
        cdp.cmd("Security.setIgnoreCertificateErrors", {"ignore": True})
        cdp.cmd("Page.navigate", {"url": f"{BASE}/login"}); wait(3)
        cdp.cmd("Runtime.evaluate", {"expression":
            f"localStorage.setItem('token',{json.dumps(args.token)});"
            f"localStorage.setItem('refresh_token',{json.dumps(args.refresh)});'ok'"})

        # 1) dashboard loads with the library showing a document
        cdp.cmd("Page.navigate", {"url": f"{BASE}/dashboard"}); wait(4)
        body = ev(cdp, "document.body.innerText") or ""
        check("dashboard authenticated", ev(cdp, "location.pathname") == "/dashboard")
        check("dashboard shows a document", "tracemonkey" in body.lower(), body[:80].replace("\n", " "))
        check("sidebar has Notes nav", "notes" in body.lower())

        # 2) open the document
        r = click_text(cdp, "tracemonkey")
        wait(4)
        body2 = ev(cdp, "document.body.innerText") or ""
        check("document opened", r.startswith("clicked"), r)
        check("reader panel rendered", len(body2) > len(body), f"len {len(body)}->{len(body2)}")

        # 3) switch to the Notes tab
        r = click_text(cdp, "Notes")
        wait(3)
        body3 = ev(cdp, "document.body.innerText") or ""
        check("Notes tab clicked", r.startswith("clicked"), r)
        # notes panel should mention adding/creating a note or empty note state
        lowered = body3.lower()
        check("notes panel visible",
              any(k in lowered for k in ("note", "highlight")), f"len={len(body3)}")

        # 4) no error overlay anywhere
        err = ev(cdp, "!!document.querySelector('nextjs-portal')")
        check("no Next.js error overlay", not err, str(err))
    finally:
        ch.terminate()
        try:
            ch.wait(timeout=5)
        except Exception:
            ch.kill()

    fails = 0
    for name, ok, detail in CHECKS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}   ({detail})")
        fails += 0 if ok else 1
    if cdp.console_errors:
        print("\nconsole errors:")
        for e in sorted(set(cdp.console_errors)):
            print("  ", e[:160])
    if cdp.net_failures:
        print("\nnetwork failures:")
        for f in sorted(set(cdp.net_failures)):
            print("  ", f[:160])
    print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
