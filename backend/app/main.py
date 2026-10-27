"""Main FastAPI application entry point."""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import settings
from app.database import init_db
from app.routers import (
    auth,
    documents,
    chat,
    tts,
    websocket,
    ollama,
    podcasts,
    flashcards,
    bookmarks,
    notes,
    settings as settings_router,
    export,
)
from app.routers import metrics as metrics_router
from app.routers import knowledge_graph
from app.services.http_metrics import MetricsMiddleware
from app.services.db_monitor import db_pool_monitor
from app.services.system_monitor import run_system_monitor, update_system_stats


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown events."""
    logger.info("Initializing OpenNoteBox...")
    # Refuse to start on a placeholder signing key. A shipped default here is
    # not a weak password, it is a published one: anyone who has seen the repo
    # can forge a token for any account. Failing loudly at boot is the only
    # point at which a self-hoster reliably notices.
    if settings.jwt_secret_is_insecure:
        raise RuntimeError(
            "JWT_SECRET is unset, a placeholder, or too short. Generate one with:\n"
            "    openssl rand -hex 32\n"
            "and set JWT_SECRET in your .env file before starting."
        )
    await init_db()
    logger.info("Database initialized")

    if settings.allow_registration:
        # The first account on an instance is bootstrapped as admin, so an
        # open registration on a reachable URL is an ownership handout, not a
        # convenience. Log it at CRITICAL so it survives log filtering.
        logger.critical(
            "ALLOW_REGISTRATION=true: anyone who reaches /api/auth/register "
            "can create an account, and the FIRST one becomes admin. Set "
            "ALLOW_REGISTRATION=false in .env once your accounts exist."
        )

    # Take an initial system snapshot so metrics are non-empty on first read
    update_system_stats()

    # Start background monitors
    db_monitor_task = asyncio.create_task(db_pool_monitor(interval=2.0))
    sys_monitor_task = asyncio.create_task(run_system_monitor(interval=5.0))

    logger.info("Monitoring started: DB pool + system resource samplers")

    yield

    db_monitor_task.cancel()
    sys_monitor_task.cancel()
    try:
        await db_monitor_task
    except asyncio.CancelledError:
        pass
    try:
        await sys_monitor_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down OpenNoteBox")


app = FastAPI(
    title="OpenNoteBox",
    description="AI-powered document reader with synced TTS highlighting",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS must be added before metrics middleware so CORS preflight responses
# are not recorded as normal requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Metrics middleware - outermost so it wraps all request handling
app.add_middleware(MetricsMiddleware)


@app.get("/health")
def health():
    return {"status": "healthy"}


app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(tts.router)
app.include_router(websocket.router)
app.include_router(ollama.router)
app.include_router(podcasts.router)
app.include_router(flashcards.router)
app.include_router(bookmarks.router)
app.include_router(notes.router)
app.include_router(settings_router.router)
app.include_router(export.router)
app.include_router(metrics_router.router)
app.include_router(knowledge_graph.router)
