"""Small, cached PDF covers generated outside the API request path."""
from pathlib import Path
import uuid

from app.config import settings


def preview_path(user_id: int, doc_id: int) -> Path:
    return Path(settings.data_dir) / "previews" / "assets" / f"{doc_id}.webp"


def preview_job_key(doc_id: int) -> str:
    return f"document-preview:{doc_id}"


def render_pdf_cover(file_path: str, destination: Path) -> None:
    """Render just the first page within a fixed pixel budget and publish atomically."""
    import pypdfium2 as pdfium

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with pdfium.PdfDocument(file_path) as pdf:
            if not len(pdf):
                raise ValueError("The PDF has no pages")
            page = pdf[0]
            try:
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    raise ValueError("The PDF page has invalid dimensions")
                bitmap = page.render(scale=min(360 / width, 480 / height))
                try:
                    image = bitmap.to_pil()
                    image.save(temporary, format="WEBP", quality=78)
                finally:
                    bitmap.close()
            finally:
                page.close()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def serve_document_preview(doc):
    """Serve or queue one immutable cover. The caller must authorize asset access."""
    import logging
    import os

    import redis
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, JSONResponse, Response

    from app.tasks.preview_tasks import generate_document_preview

    if doc.format != "pdf":
        return Response(status_code=204)
    destination = preview_path(doc.user_id, doc.id)
    if destination.is_file():
        return FileResponse(destination, media_type="image/webp",
                            headers={"Cache-Control": "private, max-age=3600"})
    if not os.path.isfile(doc.file_path):
        raise HTTPException(status_code=404, detail="Original file is unavailable")
    try:
        with redis.Redis.from_url(settings.celery_broker_url, socket_timeout=2,
                                  socket_connect_timeout=2) as client:
            key = preview_job_key(doc.id)
            if client.set(key, "queued", nx=True, ex=180):
                try:
                    generate_document_preview.delay(doc.id)
                except Exception:
                    client.delete(key)
                    raise
    except Exception:
        logging.getLogger(__name__).warning("Preview queue unavailable for document %s", doc.id, exc_info=True)
        return Response(status_code=503, headers={"Retry-After": "10"})
    return JSONResponse({"status": "queued"}, status_code=202,
                        headers={"Retry-After": "2", "Cache-Control": "no-store"})
