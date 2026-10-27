#!/usr/bin/env python3
"""Progress monitor - checks build status every 15 minutes."""
import time
import os
import subprocess
from datetime import datetime

BASE = "/home/mrx/local/github/deep_read/open-notebox"
LOG = "/tmp/opencode/progress.log"

def check_backend():
    """Check if backend files are syntactically valid."""
    try:
        r = subprocess.run(
            ["python", "-m", "py_compile", "backend/app/main.py"],
            cwd=BASE, capture_output=True, text=True, timeout=10
        )
        return r.returncode == 0
    except:
        return False

def count_files():
    """Count built files."""
    import glob
    py = glob.glob(os.path.join(BASE, "backend/**/*.py"), recursive=True)
    ts = glob.glob(os.path.join(BASE, "frontend/src/**/*.{ts,tsx}"), recursive=True)
    return len(py), len(ts)

def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    print("Progress monitor started. Checking every 15 minutes.")
    while True:
        py_count, ts_count = count_files()
        backend_ok = check_backend()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{now}] backend_py={py_count} frontend_ts={ts_count} backend_valid={'yes' if backend_ok else 'no'}"
        print(line)
        with open(LOG, "a") as f:
            f.write(line + "\n")
        time.sleep(900)

if __name__ == "__main__":
    main()
