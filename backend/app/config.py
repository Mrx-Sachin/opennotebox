from typing import List, Optional

from pydantic_settings import BaseSettings


INSECURE_JWT_SECRETS = {"CHANGE_ME", "CHANGE_ME_TO_RANDOM_HEX_32_CHARS", "", "secret"}

# Hosts that can only ever refer to a machine on the user's own network, used
# to build the default CORS origin regex below. Anchored with fullmatch by
# Starlette, so a single-label hostname alternative cannot match a public site
# (every public name has a dotted TLD).
_LOCAL_HOSTS = (
    r"localhost"
    r"|127(?:\.\d{1,3}){3}"                              # loopback
    r"|\[::1\]"                                          # loopback, v6
    r"|10(?:\.\d{1,3}){3}"                               # RFC1918
    r"|192\.168(?:\.\d{1,3}){2}"                         # RFC1918
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"        # RFC1918
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"  # CGNAT / Tailscale
    r"|169\.254(?:\.\d{1,3}){2}"                         # link-local
    r"|[A-Za-z0-9-]+\.(?:local|lan|home|internal)"       # mDNS / router-assigned
    r"|[A-Za-z0-9-]+"                                    # bare hostname, LAN-only
)
LOCAL_ORIGIN_REGEX = rf"https?://(?:{_LOCAL_HOSTS})(?::\d+)?"


class Settings(BaseSettings):
    # Ollama, or any OpenAI-compatible endpoint. host.docker.internal resolves
    # to the Docker host, so the default reaches an Ollama running natively on
    # the same machine (docker-compose.yml maps it explicitly for Linux).
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_model_summary: str = "gemma3:27b"
    ollama_model_chat: str = "qwen2.5:32b"
    # Same Ollama endpoint, different model tag.
    ollama_model_qwen: str = "qwen2.5:32b"

    # OpenAI-compatible LLM endpoint (default: local OmniRoute route via bridge).
    vllm_base_url: str = "http://host.docker.internal:8766/v1"
    vllm_model: str = "antigravity/gemini-3.8-flash-tiered"
    vllm_api_key: str = "not-needed"

    # Meta Model API / Muse Voice Transcribe
    meta_api_key: str = ""
    meta_asr_url: str = "https://api.meta.ai/v1/asr/transcribe"
    meta_asr_model: str = "muse-voice-transcribe-1.0"
    meta_llm_model: str = "muse-spark-1.3-contributor"

    # JWT
    jwt_secret: str = "CHANGE_ME"
    jwt_expiry_hours: int = 24
    # CLOSED by default. The first account registered on an instance becomes
    # the admin (bootstrap in routers/auth.py), so an open registration on a
    # reachable URL means whoever visits first owns the whole instance. A
    # fresh deployment creates its first account once with this set to true
    # (or promotes an existing user via the admin user API), then closes it.
    allow_registration: bool = False

    # Celery
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    celery_workers: int = 4

    # Database. Postgres rather than SQLite because three processes write
    # concurrently (API, Celery workers, the async engine); SQLite serialised
    # them behind a single file lock and a bulk re-parse could lock unrelated
    # API requests out for minutes ("database is locked").
    database_url: str = "postgresql+psycopg://opennotebox:opennotebox@postgres:5432/open_notebox"
    # Per-PROCESS pool limits. Several processes (API, Celery prefork children,
    # beat) each build their own pool, so the deployment-wide connection count
    # is a multiple of these - see the note in database.py before raising them.
    db_pool_size: int = 20
    db_max_overflow: int = 15

    # Storage
    upload_max_size_mb: int = 200
    # /upload-url normally refuses URLs that resolve to private/loopback
    # addresses (SSRF guard against reaching Redis/Postgres/etc. on the
    # internal network). Set true only when uploading from a file server on
    # your own network is a deliberate need.
    download_allow_private_hosts: bool = False
    tts_cache_max_gib: int = 2
    tts_cache_max_age_days: int = 7
    data_dir: str = "/app/data"
    # Root of the opt-in "export to folder" mirror (see services/export_service.py).
    # A browser cannot grant this container arbitrary host filesystem access -
    # there is no folder-picker that reaches into the host from a web UI -
    # so the actual host directory is a deployment-level decision: the
    # operator bind-mounts a host path here in docker-compose.yml (e.g.
    # ${EXPORT_DIR:-./exports}:/app/exports) and sets EXPORT_DIR to match the
    # container-side mount point. Per-user, all that's controllable is the
    # on/off toggle in UserSettings (key="export"). Empty string means
    # exporting is unavailable on this deployment regardless of any user's
    # toggle - export_service checks for that before writing anything.
    export_dir: str = "/app/exports"

    # PDF
    pdf_ocr_default: str = "auto"  # "auto" | "on" | "off"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:3003",
        "http://localhost:3013",
        "http://127.0.0.1:3003",
        "http://127.0.0.1:3013",
        "https://opennotebox.mrxsach.in",
        "https://deepread.mrxsach.in",
    ]
    # Self-hosted deployments are normally reached from more than one device on
    # the same network - a phone or laptop opens http://<host-lan-ip>:3003 and
    # the frontend then calls the API on that same hostname. Pinning CORS to a
    # single literal origin means every one of those devices loads the page and
    # then fails every request, which looks like the app being broken rather
    # than like a config gap. So private-network origins are allowed by default
    # and cors_origins stays for the public/reverse-proxied case.
    #
    # This does not widen who can reach the API (the port was already bound on
    # 0.0.0.0); it only stops the browser rejecting the app's own calls. Auth is
    # a bearer token in localStorage, which no other origin can read, so a
    # co-resident LAN page still gets nothing it could not get by asking the
    # user's browser directly. Set false to require an exact cors_origins match.
    cors_allow_private_networks: bool = True

    # TTS
    tts_url: str = "http://tts:5111"

    # Shared provider admission applies across API and Celery processes. These
    # are application-side bounds, not a claim about provider capacity. The
    # foreground lane can use 20 slots while background jobs are capped at 4,
    # leaving a bounded amount of room for uploads/readers without allowing
    # them to consume the whole interactive budget.
    llm_max_concurrency: int = 24
    llm_foreground_max_concurrency: int = 20
    llm_background_max_concurrency: int = 4
    llm_foreground_wait_seconds: float = 5.0
    llm_background_wait_seconds: float = 120.0
    llm_admission_lease_seconds: int = 90
    llm_admission_redis_url: str = "redis://redis:6379/2"
    llm_admission_key_prefix: str = "opennotebox:llm-admission"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def effective_meta_api_key(self) -> str:
        import os
        return self.meta_api_key or os.environ.get("MODEL_API_KEY", "") or os.environ.get("META_API_KEY", "")

    @property
    def cors_origin_regex(self) -> Optional[str]:
        """Extra allowed origins as a regex, or None to allow only cors_origins."""
        return LOCAL_ORIGIN_REGEX if self.cors_allow_private_networks else None

    @property
    def jwt_secret_is_insecure(self) -> bool:
        """True when JWT_SECRET was never changed from a shipped placeholder.

        Anyone who can read the repo can then mint a valid token for any
        account, so this is a full authentication bypass rather than a
        hardening nicety. Enforced at startup in main.py.
        """
        return self.jwt_secret.strip() in INSECURE_JWT_SECRETS or len(self.jwt_secret.strip()) < 16


settings = Settings()
