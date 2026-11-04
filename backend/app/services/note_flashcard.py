"""Turn one note (quote + optional body) into a scheduled flashcard."""
from __future__ import annotations

import logging
import re
from typing import Any
from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from app.models import Flashcard, Note
from app.services.flashcard_cards import create_basic_card
from app.services.json_extract import extract_json
from app.services.llm_settings import get_active_llm
from app.services.llm_semaphore import llm_semaphore, LLMAdmissionTimeout, LLMAdmissionUnavailable
from app.services.prompts import render_jinja
from app.tasks.shared import run_async

logger = logging.getLogger(__name__)

async def _generate(prompt: str, user_id: int) -> str:
    active = get_active_llm(user_id)
    model = ChatOpenAI(
        base_url=active["base_url"],
        model=active["model"],
        api_key=active["api_key"] or "EMPTY",
        temperature=0.3,
        max_tokens=2048,
        timeout=45.0,
    )
    async with llm_semaphore():
        response = await model.ainvoke([
            SystemMessage(content="You produce strict JSON only, no prose, no markdown fences."),
            HumanMessage(content=prompt),
        ])
    return response.content if hasattr(response, "content") else str(response)

def existing_card_for_note(
    db: Session, note_id: int, user_id: int | None = None
) -> Flashcard | None:
    query = db.query(Flashcard).filter(Flashcard.source_note_id == note_id)
    if user_id is not None:
        query = query.filter(Flashcard.user_id == user_id)
    return query.first()

def make_flashcard_from_note(db: Session, user_id: int, note: Note) -> Flashcard:
    existing = existing_card_for_note(db, note.id, user_id)
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

    raw = ""
    try:
        raw = run_async(_generate(prompt, user_id))
    except (LLMAdmissionTimeout, LLMAdmissionUnavailable) as e:
        logger.warning(f"Admission unavailable for note flashcard: {e}; falling back to note content")
    except Exception as e:
        logger.warning(f"LLM generation failed for note flashcard: {e}; falling back to note content")

    question = ""
    answer = ""
    hint = None

    if raw:
        try:
            data = extract_json(raw)
            if isinstance(data, dict):
                question = str(data.get("question") or data.get("Question") or data.get("front") or "").strip()
                answer = str(data.get("answer") or data.get("Answer") or data.get("back") or "").strip()
                hint = str(data.get("hint") or data.get("Hint") or "").strip() or None
        except Exception:
            pass

        if not question or not answer:
            # Regex recovery for unescaped quotes or messy formatting
            q_match = re.search(r'"(?:question|Question|front)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            a_match = re.search(r'"(?:answer|Answer|back)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            h_match = re.search(r'"(?:hint|Hint)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            if q_match:
                try:
                    question = q_match.group(1).encode("utf-8").decode("unicode_escape").strip()
                except Exception:
                    question = q_match.group(1).strip()
            if a_match:
                try:
                    answer = a_match.group(1).encode("utf-8").decode("unicode_escape").strip()
                except Exception:
                    answer = a_match.group(1).strip()
            if h_match:
                try:
                    hint = h_match.group(1).encode("utf-8").decode("unicode_escape").strip() or None
                except Exception:
                    hint = h_match.group(1).strip() or None

    # Deterministic fallback: ensure we always create a valid study card from the note
    if not question or not answer:
        if body and quote:
            question = f"What is the key takeaway of this passage:\n\"{quote[:180]}\"?"
            answer = body
        elif quote:
            clean_q = quote[:180].strip()
            question = f"What concept or finding is described in this excerpt:\n\"{clean_q}\"?"
            answer = quote
        elif body:
            question = f"Review note from page {note.page_number or 1}:"
            answer = body
        else:
            raise HTTPException(status_code=400, detail="Nothing to turn into a flashcard")

    return create_basic_card(
        db,
        user_id=user_id,
        document_id=note.document_id,
        question=question,
        answer=answer,
        hint=hint,
        page_number=note.page_number,
        source="note",
        source_note_id=note.id,
    )
