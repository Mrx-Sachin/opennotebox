FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app/ ./app/

RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/data/uploads /app/data/extracted /app/data/summaries /app/data/tts-cache

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
