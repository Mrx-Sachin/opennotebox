"""Database models aligned with PROJECT_BLUEPRINT.md Section 7."""
from datetime import datetime, timezone


from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
    Text,
    Float,
    JSON,
    ForeignKey,
    DateTime,
    UniqueConstraint,
    Index,
    text,
    event,
    inspect as sa_inspect,
)
from sqlalchemy.orm import relationship

from app.database import Base

# Knowledge graph models (HybridRAG)
from app.models.kg import KgEntity, KgRelationship, KgExtractionState  # noqa: F401


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    is_admin = Column(Boolean(), nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Documents are reusable file assets. Deleting an account must remove its
    # library membership, while leaving assets and their other members intact.
    documents = relationship("Document", back_populates="user", foreign_keys="Document.user_id")
    notebooks = relationship("Notebook", back_populates="user", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")


class Notebook(Base):
    __tablename__ = "notebooks"
    __table_args__ = (
        Index(
            "uq_notebooks_user_name_ci",
            "user_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(240), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = relationship("User", back_populates="notebooks")
    documents = relationship("Document", back_populates="notebook")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_user_id_created_at", "user_id", "created_at"),
        Index(
            "ix_documents_shared_catalog",
            "is_shared",
            "shared_at",
            "id",
        ),
        Index("uq_documents_content_hash", "content_hash", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=False)
    notebook_id = Column(
        Integer,
        ForeignKey("notebooks.id", name="fk_documents_notebook_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title = Column(Text, nullable=False)
    format = Column(String, nullable=False, index=True)
    file_path = Column(Text, nullable=False)
    file_size = Column(Integer, nullable=False)
    num_pages = Column(Integer, default=0)
    original_url = Column(Text, nullable=True)
    processing_status = Column(String, default="pending", index=True)
    layout_mode = Column(String, default="auto")  # auto, single_column, research_paper
    layout_status = Column(String(32), nullable=False, default="not_requested", server_default="not_requested")
    layout_task_id = Column(String(36), nullable=True)
    layout_error = Column(Text, nullable=True)
    layout_requested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Where the reader was last left. Stored on the document because a
    # document already belongs to exactly one user - there is no sharing - so
    # a separate per-user progress table would carry a foreign key that is
    # always implied by this row anyway.
    #
    # last_read_position is the fraction (0-1) scrolled through that page, not
    # a pixel offset: pixels are meaningless across a zoom change, a window
    # resize or a phone, all of which are normal between one session and the
    # next. A fraction survives all three.
    last_read_page = Column(Integer, nullable=True)
    last_read_position = Column(Float, nullable=True)
    last_read_at = Column(DateTime, nullable=True, index=True)

    # Shared-library identity and publisher-controlled discovery metadata.
    content_hash = Column(String(64), nullable=True)
    is_shared = Column(Boolean(), nullable=False, default=False, server_default=text("false"))
    shared_at = Column(DateTime, nullable=True)
    shared_title = Column(Text, nullable=True)
    author = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    topics = Column(JSON, nullable=True)
    document_type = Column(String(80), nullable=True)
    language = Column(String(32), nullable=True)
    shared_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    user = relationship("User", back_populates="documents", foreign_keys=[user_id])
    notebook = relationship("Notebook", back_populates="documents")
    library_entries = relationship("LibraryEntry", back_populates="document")
    document_topics = relationship(
        "DocumentTopic", back_populates="document", cascade="all, delete-orphan"
    )
    extracted_texts = relationship("ExtractedText", back_populates="document", cascade="all, delete-orphan")
    summaries = relationship("Summary", back_populates="document", cascade="all, delete-orphan")


class LibraryEntry(Base):
    """A user's membership and private reading state for a reusable asset."""

    __tablename__ = "library_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "document_id", name="uq_library_entries_user_document"),
        Index("ix_library_entries_user_notebook", "user_id", "notebook_id"),
        Index("ix_library_entries_user_added", "user_id", "added_at", "document_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    notebook_id = Column(
        Integer,
        ForeignKey("notebooks.id", name="fk_library_entries_notebook_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    added_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    title = Column(Text, nullable=True)
    original_url = Column(Text, nullable=True)
    can_publish = Column(Boolean(), nullable=False, default=False, server_default=text("false"))
    last_read_page = Column(Integer, nullable=True)
    last_read_position = Column(Float, nullable=True)
    last_read_at = Column(DateTime, nullable=True, index=True)

    user = relationship("User")
    document = relationship("Document", back_populates="library_entries")
    notebook = relationship("Notebook")


class DocumentTopic(Base):
    """Normalized index of publisher topics for indexed catalog filtering."""

    __tablename__ = "document_topics"
    __table_args__ = (
        Index("ix_document_topics_topic_document", "topic", "document_id"),
    )

    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic = Column(String(40), primary_key=True)
    document = relationship("Document", back_populates="document_topics")


@event.listens_for(Document, "after_insert")
def _create_uploader_library_entry(mapper, connection, target):
    """Give every newly created uploaded asset its uploader membership."""
    if target.user_id is None:
        return
    connection.execute(
        LibraryEntry.__table__.insert().values(
            user_id=target.user_id,
            document_id=target.id,
            notebook_id=target.notebook_id,
            title=target.title,
            original_url=target.original_url,
            can_publish=True,
            last_read_page=target.last_read_page,
            last_read_position=target.last_read_position,
            last_read_at=target.last_read_at,
        )
    )


def _sync_document_topic_index(connection, target):
    connection.execute(
        DocumentTopic.__table__.delete().where(
            DocumentTopic.__table__.c.document_id == target.id
        )
    )
    topics = list(dict.fromkeys(
        value.strip().lower()
        for value in (target.topics or [])
        if value and value.strip()
    ))
    if topics:
        connection.execute(
            DocumentTopic.__table__.insert(),
            [{"document_id": target.id, "topic": value} for value in topics],
        )


@event.listens_for(Document, "after_insert")
def _index_new_document_topics(mapper, connection, target):
    _sync_document_topic_index(connection, target)


@event.listens_for(Document, "after_update")
def _reindex_changed_document_topics(mapper, connection, target):
    if sa_inspect(target).attrs.topics.history.has_changes():
        _sync_document_topic_index(connection, target)


class ExtractedText(Base):
    __tablename__ = "extracted_text"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "page_number", name="uq_extracted_text_document_page"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False, index=True)
    content = Column(Text, nullable=False)
    bounding_boxes = Column(JSON, nullable=True)
    headings = Column(JSON, nullable=True)
    tables = Column(JSON, nullable=True)
    images = Column(JSON, nullable=True)
    layout_regions = Column(JSON, nullable=True)
    narration_exclusions = Column(JSON, nullable=True)

    document = relationship("Document", back_populates="extracted_texts")


class EmbeddingBackfill(Base):
    """Durable per-document state for background chunk embedding jobs."""

    __tablename__ = "embedding_backfills"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_embedding_backfills_document"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String(24), nullable=False, default="pending", index=True)
    requested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (
        Index("ix_summaries_document_id_mode", "document_id", "mode"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    mode = Column(String, nullable=False)
    page_number = Column(Integer, nullable=True, index=True)
    section_title = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    ai_model = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # start_page/end_page: 1-based inclusive page range for a "section" mode row,
    # used to (a) jump the reader to the section's start, and (b) pull the right
    # page content when generating this section's summary on demand.
    start_page = Column(Integer, nullable=True)
    end_page = Column(Integer, nullable=True)
    # level: outline depth for "section" mode rows (0 = top-level Part, 1 =
    # chapter within a Part), so the frontend can indent chapters under their
    # parent part. NULL for old rows generated before this column existed, or
    # for "page"-mode rows where it doesn't apply.
    level = Column(Integer, nullable=True)
    # status: None (idle - either a stub with no content yet, or a completed
    # summary), "generating" (on-demand generation in flight), "failed" (last
    # attempt produced nothing usable, e.g. a refusal). Lets the frontend poll
    # without guessing state purely from empty content.
    status = Column(String, nullable=True)

    document = relationship("Document", back_populates="summaries")


@event.listens_for(Summary, "before_insert")
def _set_legacy_summary_owner(mapper, connection, target):
    """Keep compatibility fixtures and old task callers user-scoped."""
    if target.user_id is not None or not (target.content or "").strip():
        return
    target.user_id = connection.execute(
        text("SELECT user_id FROM documents WHERE id = :document_id"),
        {"document_id": target.document_id},
    ).scalar_one_or_none()


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_id_created_at", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=False)
    document_ids = Column(JSON, nullable=True)
    page_scope = Column(JSON, nullable=True)
    title = Column(String, default="New Chat")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(JSON, nullable=True)
    token_count = Column(Integer, default=0)

    conversation = relationship("Conversation", back_populates="messages")


class TTSCache(Base):
    __tablename__ = "tts_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    summary_id = Column(Integer, ForeignKey("summaries.id", ondelete="CASCADE"), nullable=True)
    text_hash = Column(Text, nullable=False, index=True)
    audio_path = Column(Text, nullable=True)
    duration = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Bumped on every cache hit so the eviction sweep can drop least-recently
    # -used audio first. Indexed because the sweep orders by it.
    last_accessed = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    # Denormalised so eviction can total up disk usage without stat()-ing
    # every file on disk each pass.
    size_bytes = Column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint("text_hash", "user_id"),)


class Podcast(Base):
    __tablename__ = "podcasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = Column(String, nullable=False)
    section_title = Column(String, nullable=True)
    # length: "short" (~8-12 exchanges, ~5 min), "medium" (~16-24, ~10 min,
    # the historical default), or "long" (~30-45, ~20 min).
    length = Column(String, default="medium")
    status = Column(String, default="generating", index=True)
    error = Column(Text, nullable=True)
    audio_path = Column(Text, nullable=True)
    duration = Column(Float, nullable=True)
    transcript = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Flashcard(Base):
    """A single reviewable card with its FSRS scheduling state inlined on the
    row (state/step/stability/difficulty/due/last_review/reps/lapses mirror
    fsrs.Card 1:1 - see services/fsrs_scheduler.py). There is exactly one
    active schedule per card in this app, so a separate "scheduling state"
    table would only add a join to every list/queue query for no benefit.
    """
    __tablename__ = "flashcards"
    __table_args__ = (
        Index("ix_flashcards_user_id_due", "user_id", "due"),
        Index("ix_flashcards_user_id_reps", "user_id", "reps"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)

    # "basic" (plain front/back) or "cloze" (fill-in-the-blank, generated
    # from {{c1::...}} markup - see services/cloze.py). "Basic and reversed"
    # from Anki isn't its own type here: it's just two independent "basic"
    # cards created together and linked via group_id, since the scheduling
    # and review logic for each direction is identical to any basic card.
    card_type = Column(String, nullable=False, default="basic")

    # question/answer hold the fully-rendered front/back for BOTH basic and
    # cloze cards, so the review UI never needs to know how a card was
    # produced. For cloze cards these are derived from cloze_text at
    # creation/edit time (see render_cloze in services/cloze.py).
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)

    # Only set for cloze cards: the original {{c1::text::hint}} markup, kept
    # so an edit can be re-rendered consistently, plus which index (1, 2, ...)
    # this particular sibling card was generated for.
    cloze_text = Column(Text, nullable=True)
    cloze_index = Column(Integer, nullable=True)

    # Links sibling cards produced by one authoring action (the two
    # directions of a reversed pair, or the several {{cN::}} blanks of one
    # cloze note) so the UI can offer "edit/delete all siblings" without a
    # separate Note table. Set to the first sibling's own id, so "every card
    # in this group" is always a single indexed equality query.
    group_id = Column(Integer, nullable=True, index=True)

    # Distinguishes AI-generated cards from hand-authored ones purely for a
    # UI badge - both are fully editable and scheduled identically. Also
    # lets "Regenerate" replace only the AI cards, never a user's own.
    source = Column(String, nullable=False, default="ai")

    page_number = Column(Integer, nullable=True)

    # Set when this card was produced from a highlight/note. ON DELETE SET
    # NULL so deleting the note does not take the scheduled card with it.
    source_note_id = Column(
        Integer, ForeignKey("notes.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- FSRS scheduling state, mirroring fsrs.Card ---
    fsrs_state = Column(Integer, nullable=False, default=1)  # 1=Learning 2=Review 3=Relearning
    fsrs_step = Column(Integer, nullable=True)
    stability = Column(Float, nullable=True)
    difficulty = Column(Float, nullable=True)
    # due defaults to "now" so a brand-new card is immediately eligible for
    # review instead of needing a first pass to become due.
    due = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    last_review = Column(DateTime, nullable=True)
    reps = Column(Integer, nullable=False, default=0)
    lapses = Column(Integer, nullable=False, default=0)
    # Set once, the first time reps goes 0 -> 1. Lets the daily "new cards"
    # quota be computed as a plain indexed date filter instead of a
    # group-by-min over the review log on every queue fetch.
    introduced_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    review_logs = relationship("FlashcardReviewLog", back_populates="flashcard", cascade="all, delete-orphan")


class FlashcardReviewLog(Base):
    """One row per grading action (Again/Hard/Good/Easy), mirroring
    fsrs.ReviewLog. Kept both as an audit trail of study history and because
    FSRS's own per-user parameter optimizer (fsrs.Optimizer, not wired up
    here) needs this history to fit personalized weights instead of the
    library's aggregate defaults - a natural next step once a user has
    accumulated enough reviews for it to be worthwhile."""
    __tablename__ = "flashcard_review_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    flashcard_id = Column(Integer, ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1=Again 2=Hard 3=Good 4=Easy
    state_before = Column(Integer, nullable=False)
    reviewed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    review_duration_ms = Column(Integer, nullable=True)
    stability_after = Column(Float, nullable=True)
    difficulty_after = Column(Float, nullable=True)

    flashcard = relationship("Flashcard", back_populates="review_logs")


class AppSettings(Base):
    """Deployment-wide JSON key-value settings store. One row per `key`;
    `value` holds an arbitrary JSON blob.

    Multi-user note: this table is deliberately NOT per-user. It now holds
    only genuinely shared deployment infrastructure - the registry of
    configured LLM backend endpoints (key="llm_backends": name/base_url/
    model/api_key for each). Which backend is *active*, the TTS narration
    voice, and flashcard daily limits are personal preferences and live in
    UserSettings below instead, so one person's choice can't silently
    change what another person sees mid-session. See routers/settings.py
    and services/llm_settings.py for the read/write/cache logic.
    """
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class UserSettings(Base):
    """Per-user JSON key-value settings store, mirroring AppSettings but
    scoped to a single user via user_id. One row per (user_id, key) pair;
    `value` holds an arbitrary JSON blob.

    Keys currently in use:
      - "prefs": {"active": <llm backend name>, "tts": {"default_voice": ...}}
        - which of the shared AppSettings LLM backends this user has
          selected, and their TTS narration voice.
      - "flashcard_limits": {"daily_new_limit": int, "daily_review_limit": int}
        - see routers/flashcards.py.
      - "export": {"enabled": bool}
        - opt-in "export to folder" mirror (chat/flashcards/podcasts written
          to disk under settings.export_dir, in addition to staying here in
          Postgres - Postgres remains authoritative). See
          services/export_service.py, routers/export.py, and
          tasks/export_tasks.py.
      - "note_colors": {"custom": [{"id", "name", "hex"}, ...]}
        - extra highlight colours this user added on top of the built-in
          amber/green/indigo palette. See routers/notes.py.

    No ORM relationship back to User is declared here deliberately - the
    User model is owned by another engineer for this change; the FK alone
    is sufficient for cascade-delete and querying.
    """
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String, nullable=False, index=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_settings_user_key"),)


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False, index=True)
    position = Column(Float, nullable=True)
    label = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Note(Base):
    """A highlight and/or written note anchored to a passage (or a page).

    Highlight-only rows have quote set and note NULL. Page-level notes
    (no text selection) have quote NULL and a note body. Cascade-deletes
    with the document. Color is a palette id (built-in amber/green/indigo
    or a user-defined slug from UserSettings key="note_colors").
    """
    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_document_id_page_number", "document_id", "page_number"),
        Index("ix_notes_user_id_document_id", "user_id", "document_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=False)
    page_number = Column(Integer, nullable=False, index=False)
    # Inclusive end page when a highlight crosses a page boundary.
    end_page = Column(Integer, nullable=True)
    quote = Column(Text, nullable=True)
    context_before = Column(Text, nullable=True)
    context_after = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    color = Column(String, nullable=False, default="amber")
    # [start, end) character offsets in the extracted page text when known.
    # Combined with quote+context so an anchor can be re-found after a re-parse.
    word_range = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
