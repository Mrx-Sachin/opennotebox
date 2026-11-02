#!/usr/bin/env bash
# Permanent regression test runner for Whisper PDF line highlighting.
# Executes independent backend geometry tests and browser E2E tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHON_BIN="${PYTHON_BIN:-/home/mrx/.local/venv/bin/python}"
E2E_BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:3013}"
OUTPUT_DIR="${OUTPUT_DIR:-${WORKSPACE_ROOT}/_scratch/line-highlight-regression/browser}"

echo "================================================================="
echo " Whisper PDF Line-Highlighting Regression Suite"
echo " Workspace: ${WORKSPACE_ROOT}"
echo " Python:    ${PYTHON_BIN}"
echo " Base URL:  ${E2E_BASE_URL}"
echo " Output:    ${OUTPUT_DIR}"
echo "================================================================="

# 1. Verify fixtures
echo ""
echo "[Step 1/3] Verifying committed fixtures..."
"${PYTHON_BIN}" "${WORKSPACE_ROOT}/scripts/build_whisper_highlight_fixture.py" --verify

# 2. Run independent backend regression tests
echo ""
echo "[Step 2/3] Running independent backend geometry regression tests..."
PYTHONPATH="${WORKSPACE_ROOT}/backend" "${PYTHON_BIN}" -m pytest \
  "${WORKSPACE_ROOT}/backend/tests/test_whisper_line_highlight_regression.py" -v

# 3. Run Playwright E2E browser regression tests
echo ""
echo "[Step 3/3] Running Playwright browser regression tests..."
mkdir -p "${OUTPUT_DIR}"
cd "${WORKSPACE_ROOT}/e2e"
E2E_BASE_URL="${E2E_BASE_URL}" npx playwright test \
  tests/whisper-line-highlight-regression.spec.ts \
  --output "${OUTPUT_DIR}"

echo ""
echo "================================================================="
echo " All Whisper line-highlighting regression tests PASSED!"
echo "================================================================="
