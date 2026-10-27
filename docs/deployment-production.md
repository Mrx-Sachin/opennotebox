# Production images

`docker-compose.yml` is the development deployment. It builds local images and
mounts backend source for fast iteration. The production overlay uses published
images and removes those source mounts:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file .env.prod config
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file .env.prod up -d
```

Docker Compose v2.24.4 or newer is required for the `!reset` and `!override`
tags. Build and publish the backend, frontend, TTS, Traefik, Redis, and
PostgreSQL/pgvector images for the target architecture, then put each registry
reference in `.env.prod` using its immutable digest form:

```dotenv
BACKEND_IMAGE=registry.example/deepread-backend@sha256:<digest>
FRONTEND_IMAGE=registry.example/deepread-frontend@sha256:<digest>
TTS_IMAGE=registry.example/deepread-tts@sha256:<digest>
TRAEFIK_IMAGE=registry.example/traefik@sha256:<digest>
REDIS_IMAGE=registry.example/redis@sha256:<digest>
POSTGRES_IMAGE=registry.example/deepread-postgres-pgvector16@sha256:<digest>
```

The backend image build context is the repository root so it can bake in the
locked Python dependencies, Alembic migrations, prompt templates, and host-side
OmniRoute bridge script. `backend/uv.lock` is installed in frozen mode. Set the
usual secrets and database variables in the same env file. The production
overlay persists application data, exports, Postgres data, Redis data, and TTS
models in named Docker volumes. Traefik's deployment configuration and TLS
certificates remain read-only host mounts.

## Upgrading an existing compose install

Keep the existing `COMPOSE_PROJECT_NAME` in `.env.prod`; Compose uses it to
find the existing `pg_data`, `redis_data`, and `tts_models` volumes. Back up the
database before the first start of the new app image. Put the installation
in maintenance mode and stop the existing API, both workers, beat, and
frontend using its current Compose configuration before taking the backup
and copying application files. Leave PostgreSQL and Redis running. This
keeps new uploads and background writes out of the storage copy:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file .env.prod exec -T postgres pg_dump -Fc \
  -U opennotebox open_notebox > database-before-upgrade.dump
```

Check the dump by restoring it under a temporary database name before
upgrading. Substitute your configured database user if it differs from the
default shown here:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file .env.prod exec -T postgres createdb \
  -U opennotebox open_notebox_restore_check
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file .env.prod exec -T postgres pg_restore \
  -U opennotebox -d open_notebox_restore_check < database-before-upgrade.dump
```

The overlay replaces the development host paths `./data` and `./exports` with
new named volumes `${COMPOSE_PROJECT_NAME}_app_data` and
`${COMPOSE_PROJECT_NAME}_app_exports`. Copy any existing files before starting
the overlay or the app will see empty storage. For the default project name and
paths:

```sh
docker volume create open-notebox_app_data
docker run --rm \
  -v "$PWD/data:/source:ro" \
  -v open-notebox_app_data:/target \
  alpine sh -c 'cp -a /source/. /target/'
docker volume create open-notebox_app_exports
docker run --rm \
  -v "$PWD/exports:/source:ro" \
  -v open-notebox_app_exports:/target \
  alpine sh -c 'cp -a /source/. /target/'
```

PostgreSQL remains major version 16, but the image changes to one that ships
pgvector. The migration removes duplicate extracted pages and duplicate raw
chunks before adding uniqueness constraints. It keeps the newest extracted
page and prefers a duplicate chunk with an embedding. When pgvector is
available, the same migration backfills vectors and builds an HNSW index
synchronously, which can hold database locks while it scans a large chunk
table. Schedule that first start with enough maintenance time. A schema
downgrade removes the new indexes and columns but cannot recreate duplicate
rows deleted during migration; restoring the database backup is required to
recover those rows. Test the backup by restoring it to a separate database
before the upgrade, and keep the dump until the upgraded instance is accepted.

Do not use `docker compose down -v` during an image upgrade. It deletes the
named database and cache volumes.

To keep LLM work bounded across API and Celery processes, set
`LLM_MAX_CONCURRENCY`, `LLM_FOREGROUND_MAX_CONCURRENCY`, and
`LLM_BACKGROUND_MAX_CONCURRENCY` according to the backend being used. Defaults
are 24 total, up to 20 foreground requests, and up to 4 background requests.
Foreground requests wait at most 5 seconds for a slot; background work waits
at most 120 seconds. These are DeepRead admission limits and do not imply
provider capacity.

## Release validation

Run the automated workflow before publishing images. The browser checks use
isolated API fixtures and do not test Gemini or the running installation.
See [implementation validation](implementation-validation.md) for checks and
remaining deployment gates. Test the restored database and copied document
files together on a staging installation before the production upgrade.
