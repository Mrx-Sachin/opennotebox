# Updating an existing installation

Use `docker-compose.existing-release.yml` with the base Compose file when the
installation must retain its current host storage and PostgreSQL image. This
overlay removes application source mounts without switching storage to fresh
named volumes. Docker Compose v2.24.4 or newer is required.

Set these values in a private release environment file outside the repository:

- `COMPOSE_PROJECT_NAME` to the existing project name.
- `BACKEND_IMAGE` and `FRONTEND_IMAGE` to validated immutable image references.
- `EXISTING_POSTGRES_IMAGE` to the currently installed PostgreSQL image.
- `EXISTING_DATA_DIR` and `EXISTING_EXPORT_DIR` to the existing absolute paths.
- `EXISTING_CERTS_DIR` to the existing certificates directory.
- `RELEASE_CONFIG_DIR` to the validated release's absolute Traefik directory.

Preserve runtime credentials and port settings. Compare the resolved Compose
configuration against the current containers before deployment; configuration
output contains credentials, so do not publish it. Do not recreate PostgreSQL,
Redis, or TTS as part of an application-only release.

Stop the API, both workers, beat, and frontend before taking the final database
dump and archive of data and exports. Restore the dump into a disposable database
and verify document and account counts. Check the archive before upgrading.
Keep the old container image references, environment and configuration, and
backups together in a protected directory.

Deploy the validated API first and verify migration readiness before starting
workers and beat. Then start frontend and Traefik and verify authentication,
reader navigation, audio, and public security headers. Use the existing Compose
project name, and use `--no-deps` when recreating application services.

For rollback, stop application writers, restore the saved deployment
configuration and image references, and start the previous release. If the
schema changed incompatibly, restore the consistent database and files together
before starting the old application. Never run `docker compose down -v`.
