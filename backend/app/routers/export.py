"""On-demand "export to folder" trigger.

The hourly beat task (tasks/export_tasks.py) already resyncs every opted-in
user, but someone who just flipped the toggle on in Settings reasonably
expects to see files show up immediately rather than waiting up to an hour
for the next tick - hence this endpoint. Both this router and the beat task
call the same services/export_service.py so the actual file-writing logic
lives in exactly one place.

Deliberately does not require the "export" toggle to be enabled: it's a
manual, explicit action (the user clicked "Export now"), independent of
whether they've also opted into the recurring background sync. Only
requires that this deployment has EXPORT_DIR configured at all.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.routers.auth import get_current_user_from_token
from app.services.export_service import export_user_data

router = APIRouter(prefix="/api/export", tags=["export"])


@router.post("/now")
def export_now(
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    if not settings.export_dir:
        raise HTTPException(
            status_code=400,
            detail="Export is not configured on this deployment (EXPORT_DIR is unset).",
        )
    result = export_user_data(db, user)
    return {"status": "ok", **result}
