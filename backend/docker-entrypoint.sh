#!/bin/sh
# Bring the schema up to date before starting, then hand off to the command.
# The api container is the sole migration runner; the other services must
# verify the database reached the current Alembic head before doing any work.
set -eu

schema_is_at_head() {
    python - <<'PY'
import sys

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.config import settings

try:
    config = Config("/app/alembic.ini")
    expected = set(ScriptDirectory.from_config(config).get_heads())
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
    finally:
        engine.dispose()
except Exception as exc:
    print(f"Alembic readiness check failed: {exc}", file=sys.stderr)
    sys.exit(1)

if not expected or current != expected:
    print(f"Database revisions {sorted(current)} do not match Alembic heads {sorted(expected)}")
    sys.exit(1)
PY
}

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "[entrypoint] running database migrations..."
    alembic upgrade head
    if ! schema_is_at_head; then
        echo "[entrypoint] migration command completed, but the database is not at the Alembic head." >&2
        exit 1
    fi
    echo "[entrypoint] migrations complete and schema is at the Alembic head."
else
    echo "[entrypoint] waiting for the database to reach the Alembic head..."
    wait_seconds=${SCHEMA_WAIT_SECONDS:-120}
    elapsed=0
    interval=2
    while [ "$elapsed" -lt "$wait_seconds" ]; do
        if schema_is_at_head >/dev/null 2>&1; then
            echo "[entrypoint] schema is at the Alembic head."
            break
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    if [ "$elapsed" -ge "$wait_seconds" ]; then
        echo "[entrypoint] gave up waiting for the Alembic head after ${wait_seconds}s." >&2
        exit 1
    fi
fi

exec "$@"
