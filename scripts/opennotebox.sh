#!/usr/bin/env bash
# Management script for DeepRead application stack.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

COMPOSE_FILES=("-f" "docker-compose.yml")
if [[ -f "docker-compose.gpu.yml" ]]; then
  COMPOSE_FILES+=("-f" "docker-compose.gpu.yml")
fi

up() {
  echo "Starting DeepRead stack from ${DIR}..."
  docker compose "${COMPOSE_FILES[@]}" up -d "$@"
  echo "DeepRead stack started successfully."
}

down() {
  echo "Stopping DeepRead stack..."
  docker compose "${COMPOSE_FILES[@]}" down "$@"
  echo "DeepRead stack stopped."
}

restart() {
  down
  up
}

status() {
  echo "=== Docker Compose Status ==="
  docker compose "${COMPOSE_FILES[@]}" ps
  echo ""
  echo "=== Cloudflare Tunnel Status ==="
  systemctl --user status cloudflared-opennotebox.service --no-pager || true
}

logs() {
  docker compose "${COMPOSE_FILES[@]}" logs -f "$@"
}

case "${1:-status}" in
  up) shift; up "$@" ;;
  down) shift; down "$@" ;;
  restart) restart ;;
  status) status ;;
  logs) shift; logs "$@" ;;
  *) echo "usage: $0 {up|down|restart|status|logs}" >&2; exit 2 ;;
esac
