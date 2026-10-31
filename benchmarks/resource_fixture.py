"""Disposable HTTP fixture using the application's real document routes."""

import asyncio
import cProfile
import io
import json
import os
import pstats
import subprocess
import sys
import types
from pathlib import Path

from app.database import Base, SessionLocal, engine
from app.models import Document, ExtractedText, LibraryEntry, User
from app.routers import auth, documents
from app.schemas import ExtractedTextLightweightResponse, ExtractedTextResponse
from app.services.auth_service import create_access_token, get_password_hash
from fastapi import FastAPI, WebSocket
from fastapi.responses import Response, StreamingResponse
from pydantic import TypeAdapter
from sqlalchemy import event


def seed(output_path: Path) -> None:
    """Populate an isolated database and mint tokens for independent accounts."""
    Base.metadata.create_all(engine)
    password_hash = get_password_hash("demo123")
    with SessionLocal() as session:
        users = [User(username="demo" if index == 0 else f"bench{index:02}",
                      password_hash=password_hash) for index in range(30)]
        session.add_all(users)
        session.flush()
        document_ids = []
        for document_index in range(4):
            geometry = document_index == 3
            document = Document(
                user_id=users[0].id, title=f"Benchmark document {document_index}",
                format="pdf" if geometry else "txt", file_path="/unused/benchmark",
                file_size=220000, num_pages=1 if geometry else 100,
                processing_status="completed",
            )
            session.add(document)
            session.flush()
            document_ids.append(document.id)
            for page_number in range(1, document.num_pages + 1):
                session.add(ExtractedText(
                    document_id=document.id, page_number=page_number,
                    content=(f"Document {document_index}, page {page_number}. "
                             "Evidence and resource utilization are measured here.\n") * 25,
                    headings=[{"title": f"Chapter {page_number}"}],
                    bounding_boxes=[{
                        "text": f"word-{word_index}", "x": word_index / 300,
                        "y": page_number / 100, "width": 0.01, "height": 0.02,
                    } for word_index in range(250)] if geometry else None,
                    layout_regions=[{"label": "text", "bbox": [0, 0, 1, 1]}] if geometry else None,
                    narration_exclusions=None,
                ))
            session.add_all([LibraryEntry(user_id=user.id, document_id=document.id,
                                           title=document.title) for user in users[1:]])
            session.flush()
        credentials = [
            {"user_id": user.id, "token": create_access_token({"sub": str(user.id)})}
            for user in users
        ]
        session.commit()
    output_path.write_text(json.dumps({"accounts": credentials, "documents": document_ids[:3],
                                      "geometry_document": document_ids[3]}))
    output_path.chmod(0o600)


def create_app() -> FastAPI:
    """Keep the production document/auth handlers and add proxy test probes."""
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(documents.router)
    baseline = types.ModuleType("benchmark_baseline_documents")
    sys.modules[baseline.__name__] = baseline
    root = Path(__file__).resolve().parents[1]
    source = subprocess.run(
        ["git", "-C", str(root), "show",
         os.environ["BENCHMARK_BASELINE"] + ":backend/app/routers/documents.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    exec(compile(source, "baseline_documents.py", "exec"), baseline.__dict__)
    app.include_router(baseline.router, prefix="/baseline")
    query_count = 0

    @event.listens_for(engine, "before_cursor_execute")
    def count_queries(*arguments):
        nonlocal query_count
        query_count += 1

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/benchmark/query-count")
    def queries():
        return {"queries": query_count}

    @app.get("/api/benchmark/profile/{variant}")
    def profile_pages(variant: str):
        lightweight = variant == "after"
        profiler = cProfile.Profile()
        with SessionLocal() as session:
            user = session.query(User).filter(User.username == "demo").one()
            profiler.enable()
            arguments = {"doc_id": 1, "limit": 100, "offset": 0,
                         "user": user, "db": session}
            if lightweight:
                pages = documents.get_document_pages(**arguments, lightweight=True)
            else:
                pages = baseline.get_document_pages(**arguments)
            schema = ExtractedTextLightweightResponse if lightweight else ExtractedTextResponse
            adapter = TypeAdapter(list[schema])
            payload = adapter.dump_json(adapter.validate_python(pages))
            profiler.disable()
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats(20)
        return {"variant": variant, "bytes": len(payload), "profile": output.getvalue()}

    @app.get("/api/benchmark/events")
    def events():
        async def stream():
            yield "data: first\n\n"
            await asyncio.sleep(0.2)
            yield "data: last\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/benchmark/audio")
    def audio():
        return Response(b"RIFF" + bytes(4096), media_type="audio/wav")

    @app.websocket("/ws/benchmark")
    async def websocket(socket: WebSocket):
        await socket.accept()
        await socket.send_text(await socket.receive_text())
        await socket.close()

    return app


if __name__ == "__main__":
    seed(Path(os.environ["BENCHMARK_CREDENTIALS"]))
