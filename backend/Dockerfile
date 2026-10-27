FROM ghcr.io/astral-sh/uv:0.11.27 AS uv

FROM python:3.11-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --output-file /tmp/requirements.txt \
    && uv pip install --system --no-cache --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY backend/app/ ./app/
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic/ ./alembic/
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY prompts/ ./prompts/
COPY tools/omniroute_bridge.py /opt/omniroute_bridge.py

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data/uploads /app/data/extracted /app/data/summaries /app/data/tts-cache

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# --proxy-headers is REQUIRED for the auth rate limiter to work in this
# deployment: uvicorn is only ever reached through Traefik (the port publish
# is loopback-bound), so without it every request's client IP is Traefik's
# container address and the per-IP login/register buckets collapse into ONE
# global bucket - one attacker spamming logins locks every real user out.
# forwarded_allow_ips covers the Docker bridge networks Traefik connects
# from plus localhost (direct health checks); nothing else can reach this
# socket anyway, so XFF has no external forger.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"]
