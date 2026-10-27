#!/usr/bin/env bash
# Start/stop/status for the DeepRead OmniRoute bridge.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${DIR}/tools/omniroute_bridge.py"
PIDFILE="/tmp/deepread-bridge.pid"
LOGFILE="${DIR}/data/bridge.log"
PORT="${BRIDGE_PORT:-8766}"

mkdir -p "${DIR}/data"

port_pids() {
  ss -ltnp 2>/dev/null | awk -v p=":${PORT}" '
    index($0, p) {
      while (match($0, /pid=[0-9]+/)) {
        print substr($0, RSTART+4, RLENGTH-4)
        $0 = substr($0, RSTART+RLENGTH)
      }
    }'
}

is_running() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  local p
  p="$(port_pids | head -n1 || true)"
  if [[ -n "${p}" ]] && kill -0 "$p" 2>/dev/null; then
    echo "$p" >"$PIDFILE"
    return 0
  fi
  return 1
}

start() {
  if is_running; then
    echo "deepread-bridge already running (pid $(cat "$PIDFILE")) on port ${PORT}"
    return 0
  fi
  local p
  for p in $(port_pids); do
    kill "$p" 2>/dev/null || true
  done
  rm -f "$PIDFILE"
  nohup python3 "$BIN" </dev/null >>"$LOGFILE" 2>&1 &
  local py_pid=$!
  echo "$py_pid" >"$PIDFILE"
  for _ in $(seq 1 20); do
    if is_running && ss -ltn 2>/dev/null | grep -q ":${PORT}"; then
      echo "deepread-bridge started (pid $(cat "$PIDFILE")) on port ${PORT}"
      return 0
    fi
    sleep 0.1
  done
  echo "deepread-bridge failed to start; see $LOGFILE" >&2
  rm -f "$PIDFILE"
  return 1
}

stop() {
  local p
  if [[ -f "$PIDFILE" ]]; then
    p="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "${p}" ]]; then
      kill "$p" 2>/dev/null || true
      sleep 0.2
      kill -9 "$p" 2>/dev/null || true
    fi
  fi
  for p in $(port_pids); do
    kill "$p" 2>/dev/null || true
    kill -9 "$p" 2>/dev/null || true
  done
  rm -f "$PIDFILE"
  echo "deepread-bridge stopped"
}

status() {
  if is_running; then
    echo "running pid=$(cat "$PIDFILE") on port ${PORT}"
  else
    echo "stopped"
    return 1
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
