"""Celery task for generating study flashcards from a document's page summaries."""
from typing import Optional

from loguru import logger
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, Summary
from app.services.prompts import render_jinja
from app.services.json_extract import extract_json
from app.services.llm_settings import get_active_llm
from app.services.cloze import cloze_indices
from app.services.flashcard_cards import create_basic_card, create_cloze_cards
from app.tasks.shared import run_async


def _gather_content(db: Session, doc_id: int) -> str:
    """Build a page-tagged text blob from page summaries (falling back to raw pages)."""
    summaries = db.query(Summary).filter(
        Summary.document_id == doc_id,
        Summary.mode == "page",
    ).order_by(Summary.page_number).all()

    if summaries:
        return "\n\n".join(f"[page {s.page_number}]\n{s.content}" for s in summaries)

    from app.models import ExtractedText
    pages = db.query(ExtractedText).filter(
        ExtractedText.document_id == doc_id
    ).order_by(ExtractedText.page_number).all()
    return "\n\n".join(f"[page {p.page_number}]\n{p.content}" for p in pages)


async def _generate(prompt: str, user_id: Optional[int] = None) -> str:
    active = get_active_llm(user_id)
    model = ChatOpenAI(
        base_url=active["base_url"],
        model=active["model"],
        api_key=active["api_key"],
        temperature=0.4,
    )
    response = await model.ainvoke([
        SystemMessage(content="You produce strict JSON only, no prose, no markdown fences."),
        HumanMessage(content=prompt),
    ])
    return response.content


@celery_app.task(bind=True, max_retries=2)
def generate_flashcards(self, user_id: int, document_id: int) -> dict:
    """Generate 15-30 flashcards for a document and store them."""
    db: Session = SessionLocal()
    try:
        # Constrain by user_id as well as id: the task trusts whatever
        # (user_id, document_id) pair it is handed, and the cards it writes
        # are stamped with that user_id. Checking ownership here means a
        # mismatched pair fails loudly instead of quietly generating one
        # user's cards from another user's document, rather than relying
        # solely on every present and future caller having checked first.
        doc = (
            db.query(Document)
            .filter(Document.id == document_id, Document.user_id == user_id)
            .first()
        )
        if not doc:
            raise ValueError(f"Document {document_id} not found for user {user_id}")

        content = _gather_content(db, document_id)
        if not content.strip():
            logger.warning(f"No content available to generate flashcards for doc {document_id}")
            return {"document_id": document_id, "card_count": 0}

        prompt = render_jinja(
            "flashcards/generate",
            document_title=doc.title,
            content=content[:60000],
        )

        raw = run_async(_generate(prompt, user_id))
        cards = extract_json(raw)
        if not isinstance(cards, list):
            raise ValueError("Expected a JSON array of flashcards")

        count = 0
        for entry in cards:
            page = entry.get("page")
            page_number = int(page) if isinstance(page, (int, float)) else None
            card_kind = (entry.get("type") or "basic").strip().lower()

            if card_kind == "cloze":
                text = (entry.get("text") or "").strip()
                if not text or not cloze_indices(text):
                    continue  # LLM claimed cloze but produced no {{cN::}} span - skip rather than store a broken card
                new_cards = create_cloze_cards(
                    db, user_id=user_id, document_id=document_id, cloze_text=text,
                    page_number=page_number, source="ai",
                )
                count += len(new_cards)
                continue

            # Default/legacy shape: plain question+answer (also what older
            # prompt responses without a "type" field produce).
            question = (entry.get("question") or "").strip()
            answer = (entry.get("answer") or "").strip()
            if not question or not answer:
                continue
            create_basic_card(
                db, user_id=user_id, document_id=document_id, question=question, answer=answer,
                page_number=page_number, source="ai",
            )
            count += 1

        db.commit()
        logger.info(f"Generated {count} flashcards for doc {document_id}")
        return {"document_id": document_id, "card_count": count}

    except Exception as e:
        logger.error(f"Flashcard generation failed for doc {document_id}: {e}")
        db.rollback()
        raise self.retry(exc=e, countdown=30)
    finally:
        db.close()
