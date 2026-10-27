"""Notebook folders for organizing a user's documents."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LibraryEntry, Notebook, User
from app.routers.auth import get_current_user_from_token
from app.schemas import (
    NotebookCreate,
    NotebookMoveDocumentsRequest,
    NotebookResponse,
    NotebookUpdate,
)

router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])


def _get_owned_notebook(db: Session, notebook_id: int, user_id: int) -> Notebook:
    notebook = db.query(Notebook).filter(
        Notebook.id == notebook_id,
        Notebook.user_id == user_id,
    ).with_for_update().first()
    if notebook is None:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return notebook


def _name_conflicts(
    db: Session, user_id: int, name: str, excluding_id: int | None = None
) -> bool:
    query = db.query(Notebook.id).filter(
        Notebook.user_id == user_id,
        func.lower(Notebook.name) == name.lower(),
    )
    if excluding_id is not None:
        query = query.filter(Notebook.id != excluding_id)
    return query.first() is not None


@router.get("", response_model=list[NotebookResponse])
def list_notebooks(
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    counts = (
        db.query(LibraryEntry.notebook_id, func.count(LibraryEntry.id))
        .filter(LibraryEntry.user_id == user.id, LibraryEntry.notebook_id.isnot(None))
        .group_by(LibraryEntry.notebook_id)
        .subquery()
    )
    rows = (
        db.query(Notebook, func.coalesce(counts.c[1], 0).label("document_count"))
        .outerjoin(counts, counts.c.notebook_id == Notebook.id)
        .filter(Notebook.user_id == user.id)
        .order_by(Notebook.created_at.desc(), Notebook.id.desc())
        .all()
    )
    return [
        {
            "id": notebook.id,
            "name": notebook.name,
            "description": notebook.description,
            "document_count": document_count,
            "created_at": notebook.created_at,
            "updated_at": notebook.updated_at,
        }
        for notebook, document_count in rows
    ]


@router.post("", response_model=NotebookResponse, status_code=status.HTTP_201_CREATED)
def create_notebook(
    payload: NotebookCreate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    if _name_conflicts(db, user.id, payload.name):
        raise HTTPException(status_code=409, detail="A notebook with this name already exists")
    notebook = Notebook(
        user_id=user.id,
        name=payload.name,
        description=payload.description,
    )
    db.add(notebook)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _name_conflicts(db, user.id, payload.name):
            raise HTTPException(
                status_code=409,
                detail="A notebook with this name already exists",
            ) from exc
        raise
    db.refresh(notebook)
    return {
        "id": notebook.id,
        "name": notebook.name,
        "description": notebook.description,
        "document_count": 0,
        "created_at": notebook.created_at,
        "updated_at": notebook.updated_at,
    }


@router.post("/move-documents")
def move_documents(
    payload: NotebookMoveDocumentsRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Move an owned batch of documents, or leave the whole batch unchanged."""
    if payload.notebook_id is not None:
        _get_owned_notebook(db, payload.notebook_id, user.id)

    document_ids = set(payload.document_ids)
    documents = (
        db.query(LibraryEntry)
        .filter(LibraryEntry.document_id.in_(document_ids), LibraryEntry.user_id == user.id)
        .with_for_update()
        .all()
    )
    if len(documents) != len(document_ids):
        raise HTTPException(status_code=404, detail="One or more documents not found")

    db.query(LibraryEntry).filter(
        LibraryEntry.document_id.in_(document_ids),
        LibraryEntry.user_id == user.id,
    ).update({LibraryEntry.notebook_id: payload.notebook_id}, synchronize_session=False)
    db.commit()
    return {"moved": len(document_ids)}


@router.patch("/{notebook_id}", response_model=NotebookResponse)
def update_notebook(
    notebook_id: int,
    payload: NotebookUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    notebook = _get_owned_notebook(db, notebook_id, user.id)
    values = payload.model_dump(exclude_unset=True)
    name = values.pop("name", None)
    if name is not None:
        if _name_conflicts(db, user.id, name, excluding_id=notebook.id):
            raise HTTPException(status_code=409, detail="A notebook with this name already exists")
        notebook.name = name
    for field, value in values.items():
        setattr(notebook, field, value)
    if name is not None or values:
        notebook.updated_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if name is not None and _name_conflicts(
                db, user.id, name, excluding_id=notebook.id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="A notebook with this name already exists",
                ) from exc
            raise
        db.refresh(notebook)

    count = db.query(LibraryEntry.id).filter(
        LibraryEntry.user_id == user.id,
        LibraryEntry.notebook_id == notebook.id,
    ).count()
    return {
        "id": notebook.id,
        "name": notebook.name,
        "description": notebook.description,
        "document_count": count,
        "created_at": notebook.created_at,
        "updated_at": notebook.updated_at,
    }


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notebook(
    notebook_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    notebook = _get_owned_notebook(db, notebook_id, user.id)
    db.query(LibraryEntry).filter(
        LibraryEntry.user_id == user.id,
        LibraryEntry.notebook_id == notebook.id,
    ).update({LibraryEntry.notebook_id: None}, synchronize_session=False)
    db.delete(notebook)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
