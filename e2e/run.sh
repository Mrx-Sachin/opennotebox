#!/usr/bin/env bash
# Seed the dummy account and run the browser suite. Needs the stack up:
#   docker compose up -d
# Then:
#   ./e2e/run.sh
set -euo pipefail
cd "$(dirname "$0")"

export E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:3003}"
export E2E_API_URL="${E2E_API_URL:-http://127.0.0.1:3003}"
export E2E_USER="${E2E_USER:-e2e.dummy}"
export E2E_PASSWORD="${E2E_PASSWORD:-E2eDummy123!}"

if [[ ! -f fixtures/e2e-sample.pdf ]]; then
  python3 fixtures/make_pdf.py
fi

python3 seed.py

if [[ ! -d node_modules/@playwright/test ]]; then
  npm install
  npx playwright install chromium
fi

npx playwright test "$@"
