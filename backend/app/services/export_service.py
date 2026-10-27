"""Export-to-folder service: mirrors a user's chat conversations, flashcard
decks, and podcasts to human-readable files under settings.export_dir, for
users who opt in (UserSettings key="export", see models/__init__.py).

Postgres remains authoritative. This module only ever reads from the DB and
writes files - it never deletes or mutates a DB row - so a bug here cannot
lose app data, only produce a stale or incomplete mirror on disk. Called
from two places sharing this single implementation (per the "one place, not
duplicated" requirement):
  - routers/export.py's POST /api/export/now (manual, synchronous, one user)
  - tasks/export_tasks.py's hourly beat task (every opted-in user)

Known limitation: this is a forward mirror, not a full two-way sync. If a
conversation or podcast is deleted from the app after having been exported,
the corresponding file is not removed from EXPORT_DIR on the next sync -
tracking deletions would need a manifest of what was previously written,
which felt like more machinery than an export mirror warrants. Flashcards
are the exception: flashcards.json is fully rewritten (or removed if the
document now has zero cards) every sync, since it is one file per document
rather than one file per row.
"""
import json
import os
import re
import shutil
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Conversation, Document, Flashcard, LibraryEntry, Message, Podcast, User

# UserSettings key. Defined here (not in routers/settings.py) so that both
# the router and the Celery task import it from the same place they import
# export_user_data from, rather than routers -> tasks or tasks -> routers
# creating an import direction that doesn't otherwise exist in this codebase.
EXPORT_KEY = "export"

_UNFILED_DIR_NAME = "_unfiled"
_MAX_SEGMENT_LEN = 150


def sanitize_path_segment(name: str, fallback: str) -> str:
    """Turn a user-controlled string (username, document title) into one
    safe path segment.

    This is a real security boundary, not cosmetic: usernames and document
    titles are entirely user-chosen and end up directly in filesystem paths
    under EXPORT_DIR. Without this, a document titled "../../etc/passwd" or
    containing a NUL byte could write outside the intended per-user
    directory. Path separators are stripped first so "a/../../x" can't
    reassemble into a traversal after the rest of the cleanup runs.
    """
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"[\x00-\x1f]", "", name)  # control chars, incl. NUL
    name = re.sub(r'[<>:"|?*]', "_", name)  # illegal on Windows filesystems too
    name = name.strip(" .")  # trailing dots/spaces are stripped silently by Windows
    name = re.sub(r"\s+", " ", name)
    name = name[:_MAX_SEGMENT_LEN]
    # ".", ".." and "" would otherwise resolve to the parent directory (or
    # collide with every other user/document that also sanitizes to
    # nothing), rather than a genuinely empty-named entry.
    if name in ("", ".", ".."):
        return fallback
    return name


def _user_dir(user: User) -> str:
    return os.path.join(settings.export_dir, sanitize_path_segment(user.username, f"user-{user.id}"))


def _document_dir(
    user: User, document: Optional[Document], title: Optional[str] = None
) -> str:
    """Directory for one document's exported files, or a shared "_unfiled"
    bucket for chat conversations that aren't scoped to any (owned)
    document. Two documents whose titles sanitize to the same string will
    collide into one directory - accepted as a known limitation rather than
    suffixing every folder with a document id, which would make the export
    directory considerably less human-readable for the common case.
    """
    if document is None:
        return os.path.join(_user_dir(user), _UNFILED_DIR_NAME)
    visible_title = (
        title
        or (document.shared_title if document.is_shared else None)
        or f"document-{document.id}"
    )
    segment = sanitize_path_segment(visible_title, f"document-{document.id}")
    return os.path.join(_user_dir(user), segment)


def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _conversation_markdown(conversation: Conversation, messages: List[Message]) -> str:
    lines = [
        f"# {conversation.title or 'Untitled conversation'}",
        "",
        f"- Conversation ID: {conversation.id}",
        f"- Created: {conversation.created_at.isoformat() if conversation.created_at else 'unknown'}",
        "",
        "---",
        "",
    ]
    role_labels = {"user": "User", "assistant": "Assistant"}
    for m in messages:
        label = role_labels.get(m.role, m.role.capitalize())
        lines.append(f"**{label}:**")
        lines.append("")
        lines.append(m.content or "")
        lines.append("")
    return "\n".join(lines)


def _export_chat(
    db: Session,
    user: User,
    documents: Dict[int, Document],
    document_titles: Dict[int, str],
) -> int:
    conversations = db.query(Conversation).filter(Conversation.user_id == user.id).all()
    count = 0
    for conv in conversations:
        # A conversation can span multiple documents (or none, for a
        # general chat with no scope). It's mirrored into every owned
        # document's folder it touches - and into _unfiled if it touches
        # none - rather than picking one arbitrarily, so it's discoverable
        # from wherever a user would naturally go looking for it.
        target_docs: List[Optional[Document]] = [
            documents[doc_id] for doc_id in (conv.document_ids or []) if doc_id in documents
        ]
        if not target_docs:
            target_docs = [None]

        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.id.asc())
            .all()
        )
        if not messages:
            continue

        created = conv.created_at.strftime("%Y-%m-%d") if conv.created_at else "undated"
        filename = f"{created}-{conv.id}.md"
        content = _conversation_markdown(conv, messages)
        for doc in target_docs:
            doc_dir = _document_dir(user, doc, document_titles.get(doc.id))
            try:
                _write_text(os.path.join(doc_dir, "chat", filename), content)
                count += 1
            except OSError as e:
                logger.warning(f"export_service: failed to write conversation {conv.id} for user {user.id}: {e}")
    return count


def _flashcard_to_dict(card: Flashcard) -> dict:
    return {
        "id": card.id,
        "card_type": card.card_type,
        "question": card.question,
        "answer": card.answer,
        "cloze_text": card.cloze_text,
        "cloze_index": card.cloze_index,
        "group_id": card.group_id,
        "source": card.source,
        "page_number": card.page_number,
        "fsrs_state": card.fsrs_state,
        "due": card.due.isoformat() if card.due else None,
        "last_review": card.last_review.isoformat() if card.last_review else None,
        "reps": card.reps,
        "lapses": card.lapses,
        "stability": card.stability,
        "difficulty": card.difficulty,
        "created_at": card.created_at.isoformat() if card.created_at else None,
    }


def _export_flashcards(
    db: Session,
    user: User,
    documents: Dict[int, Document],
    document_titles: Dict[int, str],
) -> int:
    count = 0
    for doc_id, document in documents.items():
        cards = (
            db.query(Flashcard)
            .filter(Flashcard.user_id == user.id, Flashcard.document_id == doc_id)
            .order_by(Flashcard.id.asc())
            .all()
        )
        path = os.path.join(
            _document_dir(user, document, document_titles.get(doc_id)),
            "flashcards.json",
        )
        if not cards:
            # Rewritten (not appended) every sync, so a document that had
            # its last card deleted since the previous sync must have its
            # stale flashcards.json removed too - otherwise the mirror
            # would claim cards exist that no longer do anywhere.
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            continue
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump([_flashcard_to_dict(c) for c in cards], f, indent=2)
            count += 1
        except OSError as e:
            logger.warning(f"export_service: failed to write flashcards for document {doc_id}: {e}")
    return count


def _export_podcasts(
    db: Session,
    user: User,
    documents: Dict[int, Document],
    document_titles: Dict[int, str],
) -> int:
    podcasts = (
        db.query(Podcast)
        .filter(Podcast.user_id == user.id, Podcast.status == "completed")
        .all()
    )
    count = 0
    for podcast in podcasts:
        document = documents.get(podcast.document_id)
        if document is None:
            continue
        doc_dir = _document_dir(user, document, document_titles.get(podcast.document_id))
        meta = {
            "id": podcast.id,
            "document_id": podcast.document_id,
            "scope": podcast.scope,
            "section_title": podcast.section_title,
            "length": podcast.length,
            "duration": podcast.duration,
            "transcript": podcast.transcript,
            "created_at": podcast.created_at.isoformat() if podcast.created_at else None,
        }
        try:
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, f"podcast-{podcast.id}.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            if podcast.audio_path and os.path.exists(podcast.audio_path):
                # Piper (tts/main.py) writes WAV, not MP3, despite the
                # "podcast-<id>.mp3" example in the original layout sketch -
                # using the real extension from audio_path here rather than
                # hardcoding .mp3 so the exported file is actually playable
                # as what it claims to be.
                ext = os.path.splitext(podcast.audio_path)[1] or ".wav"
                shutil.copyfile(podcast.audio_path, os.path.join(doc_dir, f"podcast-{podcast.id}{ext}"))
            count += 1
        except OSError as e:
            logger.warning(f"export_service: failed to export podcast {podcast.id}: {e}")
    return count


def export_user_data(db: Session, user: User) -> dict:
    """Run a full export sync for one user. Safe to call repeatedly (a
    resync just overwrites files with current content); safe to call when
    export_dir is unset (returns immediately) so callers don't each need
    their own guard.
    """
    if not settings.export_dir:
        return {"skipped": "export_dir not configured"}

    rows = (
        db.query(Document, LibraryEntry.title)
        .join(LibraryEntry, LibraryEntry.document_id == Document.id)
        .filter(LibraryEntry.user_id == user.id)
        .all()
    )
    documents = {document.id: document for document, _ in rows}
    document_titles = {
        document.id: title
        for document, title in rows
        if title
    }

    try:
        os.makedirs(_user_dir(user), exist_ok=True)
    except OSError as e:
        logger.error(f"export_service: could not create export dir for user {user.id}: {e}")
        return {"error": str(e)}

    conversations = _export_chat(db, user, documents, document_titles)
    flashcard_docs = _export_flashcards(db, user, documents, document_titles)
    podcasts = _export_podcasts(db, user, documents, document_titles)

    return {
        "conversations_exported": conversations,
        "flashcard_files_written": flashcard_docs,
        "podcasts_exported": podcasts,
    }
