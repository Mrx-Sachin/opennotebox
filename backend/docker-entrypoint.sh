#!/bin/sh
# Bring the schema up to date before starting, then hand off to the command.
#
# Four containers share this image (api, worker, worker-tts, beat). Only ONE
# may run migrations - concurrent `alembic upgrade head` from several
# containers races on the alembic_version row and can deadlock or double-apply.
# The api container is elected via RUN_MIGRATIONS=1 in docker-compose.yml;
# everyone else waits for it to finish instead, so no worker ever starts
# issuing queries against a half-created schema.
#
# Migrating here rather than in the app's lifespan keeps a failure loud and
# fatal: the container exits instead of serving traffic against a broken
# schema.
set -e

if [ "${RUN_MIGRATIONS}" = "1" ]; then
    echo "[entrypoint] running database migrations..."
    alembic upgrade head
    echo "[entrypoint] migrations complete."
else
    echo "[entrypoint] waiting for migrations to be applied..."
    i=0
    while [ "$i" -lt 60 ]; do
        if python -c "
import sys
from sqlalchemy import create_engine, inspect
from app.config import settings
try:
    insp = inspect(create_engine(settings.database_url))
    sys.exit(0 if 'alembic_version' in insp.get_table_names() else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            echo "[entrypoint] schema is ready."
            break
        fi
        i=$((i + 1))
        sleep 2
    done
    if [ "$i" -ge 60 ]; then
        echo "[entrypoint] gave up waiting for the schema after 120s; starting anyway." >&2
    fi
fi

exec "$@"
