"""Turn one note (quote + optional body) into a scheduled flashcard."""
from __future__ import annotations

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from app.models import Flashcard, Note
from app.services.flashcard_cards import create_basic_card
from app.services.json_extract import extract_json
from app.services.llm_settings import get_active_llm
from app.services.prompts import render_jinja
from app.tasks.shared import run_async


async def _generate(prompt: str, user_id: int) -> str:
    active = get_active_llm(user_id)
    model = ChatOpenAI(
        base_url=active["base_url"],
        model=active["model"],
        api_key=active["api_key"] or "EMPTY",
        temperature=0.3,
    )
    response = await model.ainvoke([
        SystemMessage(content="You produce strict JSON only, no prose, no markdown fences."),
        HumanMessage(content=prompt),
    ])
    return response.content


def existing_card_for_note(db: Session, note_id: int) -> Flashcard | None:
    return db.query(Flashcard).filter(Flashcard.source_note_id == note_id).first()


def make_flashcard_from_note(db: Session, user_id: int, note: Note) -> Flashcard:
    existing = existing_card_for_note(db, note.id)
    if existing:
        return existing

    quote = (note.quote or "").strip()
    body = (note.note or "").strip()
    if not quote and not body:
        raise HTTPException(status_code=400, detail="Nothing to turn into a flashcard")

    prompt = render_jinja(
        "flashcards/from_note",
        quote=quote or "(no quote)",
        note=body or "(no written note)",
        page_number=note.page_number,
    )
    raw = run_async(_generate(prompt, user_id))
    data = extract_json(raw)
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Model did not return a flashcard")
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not question or not answer:
        raise HTTPException(status_code=502, detail="Model returned an empty flashcard")

    return create_basic_card(
        db,
        user_id=user_id,
        document_id=note.document_id,
        question=question,
        answer=answer,
        page_number=note.page_number,
        source="note",
        source_note_id=note.id,
    )
