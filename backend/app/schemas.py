from app.voice_catalog import DEFAULT_VOICE, KokoroVoiceId
from datetime import datetime
from typing import Annotated, Dict, Optional, List
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    # Capped at 72 bytes because bcrypt (5.x) raises ValueError past that
    # point instead of silently truncating like older versions did - a
    # max_length=100 here would let registration 500 on a long passphrase.
    password: str = Field(..., min_length=6, max_length=72)


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    """Partial update - only admin field can be changed by an admin."""
    is_admin: Optional[bool] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class DocumentCreate(BaseModel):
    title: Optional[str] = None
    format: Optional[str] = None
    layout_mode: Optional[str] = "auto"


class DocumentResponse(BaseModel):
    id: int
    user_id: int
    title: str
    format: str
    file_size: int
    notebook_id: Optional[int] = None
    num_pages: int
    processing_status: str
    original_url: Optional[str] = None
    layout_mode: Optional[str] = "auto"
    created_at: datetime
    last_read_page: Optional[int] = None
    last_read_position: Optional[float] = None
    last_read_at: Optional[datetime] = None
    can_share: bool = False
    is_shared: bool = False
    shared_at: Optional[datetime] = None
    shared_title: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    document_type: Optional[str] = None
    language: Optional[str] = None
    read_only: bool = False
    model_config = ConfigDict(from_attributes=True)


class SharedDocumentUpdate(BaseModel):
    """Publisher-owned metadata for a document in the public catalog."""

    is_shared: bool
    title: Optional[str] = Field(default=None, max_length=300)
    author: Optional[str] = Field(default=None, max_length=240)
    description: Optional[str] = Field(default=None, max_length=2000)
    topics: Optional[List[str]] = Field(default=None, max_length=8)
    document_type: Optional[str] = Field(default=None, max_length=80)
    language: Optional[str] = Field(default=None, max_length=32)

    @field_validator("title", "document_type")
    @classmethod
    def require_nonblank_shared_metadata(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Shared metadata must not be blank")
        return trimmed

    @field_validator("author", "description", "language")
    @classmethod
    def normalize_optional_shared_metadata(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() or None if value is not None else None

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        if values is None:
            return None
        normalized = []
        seen = set()
        for topic in values:
            value = topic.strip().lower()
            if not value:
                continue
            if len(value) > 40:
                raise ValueError("Topics must be at most 40 characters")
            if value not in seen:
                normalized.append(value)
                seen.add(value)
        if len(normalized) > 8:
            raise ValueError("At most 8 unique topics are allowed")
        return normalized


class SharedLibraryDocument(BaseModel):
    id: int
    title: str
    author: Optional[str] = None
    description: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    document_type: Optional[str] = None
    language: Optional[str] = None
    num_pages: int
    file_size: int
    shared_at: Optional[datetime] = None
    added: bool
    reason: Optional[str] = None


class SharedLibraryResponse(BaseModel):
    documents: List[SharedLibraryDocument]
    total: int
    topics: List[str]
    document_types: List[str]


class SharedLibraryAddRequest(BaseModel):
    document_ids: List[Annotated[int, Field(ge=1)]] = Field(..., min_length=1, max_length=200)
    notebook_id: Optional[int] = Field(default=None, ge=1)


class SharedLibraryAddResponse(BaseModel):
    added: int
    already_added: int
    document_ids: List[int]


class NotebookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=240)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value


class NotebookUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=240)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank")
        return value


class NotebookResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    document_count: int
    created_at: datetime
    updated_at: datetime


class NotebookMoveDocumentsRequest(BaseModel):
    document_ids: list[Annotated[int, Field(ge=1)]] = Field(..., min_length=1, max_length=200)
    notebook_id: Optional[int] = Field(default=None, ge=1)


class DocumentLibraryResponse(BaseModel):
    documents: List[DocumentResponse]
    total: int
    library_total: int
    reading_count: int
    processing_count: int
    failed_count: int
    formats: Dict[str, int]
    recent: List[DocumentResponse]


class ReadingProgressIn(BaseModel):
    """Where the reader currently is. Sent while reading, not on a save button."""
    page: int = Field(ge=1)
    # Fraction through the page, so it survives zoom, resize and a different
    # device - none of which preserve a pixel offset.
    position: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ExtractedTextResponse(BaseModel):
    id: int
    document_id: int
    page_number: int
    content: str
    bounding_boxes: Optional[List] = None
    headings: Optional[List] = None
    layout_regions: Optional[List] = None
    narration_exclusions: Optional[List] = None
    model_config = ConfigDict(from_attributes=True)


class SummaryResponse(BaseModel):
    id: int
    document_id: int
    mode: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    content: str
    ai_model: Optional[str] = None
    created_at: datetime
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    status: Optional[str] = None
    level: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class SectionSummaryGenerateRequest(BaseModel):
    section_title: str


class PageSummaryGenerateRequest(BaseModel):
    page_number: int


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    document_ids: Optional[List[int]] = None
    page_scope: Optional[dict] = None
    rag_mode: Optional[str] = None  # "standard" | "hybrid" | "graph" - reserved for future RAG modes


class ChatScopeUpdate(BaseModel):
    """Update the document/page scope on a conversation without sending a message."""
    document_ids: Optional[List[int]] = None
    page_scope: Optional[dict] = None


class CitationEvidence(BaseModel):
    exact: str
    model_config = ConfigDict(extra="allow")

    @field_validator("exact")
    @classmethod
    def validate_exact(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Citation evidence exact must not be empty or whitespace")
        return v


class Citation(BaseModel):
    n: Optional[int] = None
    doc_id: Optional[int] = None
    doc_title: Optional[str] = None
    page: Optional[int] = None
    excerpt: str = ""
    evidence: Optional[CitationEvidence] = None
    model_config = ConfigDict(from_attributes=True, extra="allow")

    @model_serializer(mode="wrap")
    def serialize_citation(self, handler):
        data = handler(self)
        if data.get("evidence") is None:
            data.pop("evidence", None)
        return data


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    citations: Optional[List[Citation]] = None
    token_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class ConversationResponse(BaseModel):
    id: int
    user_id: int
    title: str
    document_ids: Optional[list] = None
    page_scope: Optional[dict] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PodcastCreate(BaseModel):
    document_id: int
    scope: str = Field(..., pattern="^(full|section)$")
    section_title: Optional[str] = None
    length: str = Field("medium", pattern="^(short|medium|long|deep_dive)$")


class PodcastResponse(BaseModel):
    id: int
    user_id: int
    document_id: int
    scope: str
    section_title: Optional[str] = None
    length: Optional[str] = "medium"
    status: str
    error: Optional[str] = None
    duration: Optional[float] = None
    transcript: Optional[dict] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class FlashcardGenerateRequest(BaseModel):
    document_id: int


class FlashcardResponse(BaseModel):
    id: int
    document_id: int
    card_type: str
    question: str
    answer: str
    cloze_text: Optional[str] = None
    cloze_index: Optional[int] = None
    group_id: Optional[int] = None
    source: str
    page_number: Optional[int] = None
    source_note_id: Optional[int] = None
    fsrs_state: int
    due: datetime
    last_review: Optional[datetime] = None
    reps: int
    lapses: int
    stability: Optional[float] = None
    difficulty: Optional[float] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class FlashcardQueueItem(FlashcardResponse):
    # Next interval each of the four grades would produce, e.g.
    # {"again": "10m", "hard": "1d", "good": "3d", "easy": "7d"} - shown
    # under the grade buttons the way Anki does.
    preview_intervals: dict = {}


class FlashcardCreate(BaseModel):
    document_id: int
    card_type: str = Field("basic", pattern="^(basic|reversed|cloze)$")
    # basic/reversed use question+answer; cloze uses cloze_text (Anki
    # {{c1::...}} markup) instead.
    question: Optional[str] = None
    answer: Optional[str] = None
    cloze_text: Optional[str] = None
    page_number: Optional[int] = None


class FlashcardUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    cloze_text: Optional[str] = None
    page_number: Optional[int] = None


class FlashcardReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=4)  # 1=Again 2=Hard 3=Good 4=Easy
    duration_ms: Optional[int] = None


class FlashcardStats(BaseModel):
    due: int
    new: int
    total: int
    new_remaining_today: int
    review_remaining_today: int


class FlashcardSettings(BaseModel):
    daily_new_limit: int = Field(20, ge=0, le=1000)
    daily_review_limit: int = Field(200, ge=0, le=5000)


class BookmarkCreate(BaseModel):
    document_id: int
    page_number: int
    position: Optional[float] = None
    # Optional; server defaults to "Page {n}" when missing/blank.
    label: Optional[str] = None


class BookmarkUpdate(BaseModel):
    """Partial update - currently only rename (label)."""
    label: str


class BookmarkResponse(BaseModel):
    id: int
    document_id: int
    page_number: int
    position: Optional[float] = None
    label: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LLMBackendIn(BaseModel):
    """A backend as submitted by the client on PUT /api/settings. api_key is
    write-only - accepted here, never echoed back (see LLMBackendOut)."""
    name: str
    base_url: str
    model: str
    api_key: Optional[str] = None


class LLMBackendOut(BaseModel):
    """A backend as returned by GET /api/settings. Never includes the raw
    api_key value, only whether one is set."""
    name: str
    base_url: str
    # The registry's own model - deployment-wide, shared by every user. This
    # is what a client must round-trip when it PUTs `backends` back.
    model: str
    # What THIS user's requests actually run against: `model` with their
    # personal override applied, if they set one. Read-only; change it by
    # sending llm.model_overrides, never by rewriting `model`, which would
    # push one user's choice onto everybody on the deployment.
    effective_model: str = ""
    api_key_set: bool = False


class LLMSettingsIn(BaseModel):
    backends: Optional[List[LLMBackendIn]] = None
    active: Optional[str] = None
    # {backend name: model id} for THIS user only. Merged into the user's
    # existing overrides rather than replacing them, so a client can change
    # one backend without having to know the rest. Map a backend to null to
    # drop its override and fall back to the registry model.
    model_overrides: Optional[Dict[str, Optional[str]]] = None


class LLMSettingsOut(BaseModel):
    backends: List[LLMBackendOut]
    active: str


class TTSSettings(BaseModel):
    default_voice: KokoroVoiceId = DEFAULT_VOICE


class ExportSettings(BaseModel):
    """The 'export to folder' opt-in toggle (UserSettings key="export"). Just
    on/off - the actual host directory (EXPORT_DIR) is a deployment-level
    setting, not something a per-user request body can influence; see
    config.py."""
    enabled: bool = False


class SettingsUpdate(BaseModel):
    """Partial update body for PUT /api/settings. Any field omitted is left
    unchanged."""
    llm: Optional[LLMSettingsIn] = None
    tts: Optional[TTSSettings] = None
    export: Optional[ExportSettings] = None


class SettingsResponse(BaseModel):
    llm: LLMSettingsOut
    tts: TTSSettings
    export: ExportSettings


class LLMBackendHealthOut(BaseModel):
    """One entry of GET /api/settings/llm/health: the probe result for a
    single configured backend, as seen from the API container."""
    name: str
    # "healthy" (endpoint answered /v1/models), "degraded" (answered but
    # with an unexpected status), or "unhealthy" (no response at all).
    status: str
    latency_ms: Optional[float] = None
    # healthy: the endpoint's model list (capped); unhealthy: the probe
    # error; degraded: http_status is set instead.
    available_models: Optional[List[str]] = None
    error: Optional[str] = None
    http_status: Optional[int] = None


class LLMHealthResponse(BaseModel):
    """Per-backend health for the deployment-wide registry, plus which
    backend THIS user has active - enough for the UI to mark a dead
    bridge as dead instead of showing a stale "Connected"."""
    active: str
    backends: List[LLMBackendHealthOut]


class LLMProbeIn(BaseModel):
    """Body for POST /api/settings/llm/probe: list models on an endpoint
    that is not necessarily saved in the registry yet."""
    base_url: str
    api_key: Optional[str] = None


class LLMProbeOut(BaseModel):
    status: str
    latency_ms: Optional[float] = None
    available_models: Optional[List[str]] = None
    error: Optional[str] = None
    http_status: Optional[int] = None


class ProgressEvent(BaseModel):
    task_id: str
    status: str
    progress: int = 0
    message: Optional[str] = None
    page_number: Optional[int] = None
    total_pages: Optional[int] = None


class NoteCreate(BaseModel):
    document_id: int
    page_number: int = Field(..., ge=1)
    end_page: Optional[int] = Field(None, ge=1)
    quote: Optional[str] = Field(None, max_length=20000)
    context_before: Optional[str] = Field(None, max_length=500)
    context_after: Optional[str] = Field(None, max_length=500)
    note: Optional[str] = Field(None, max_length=20000)
    color: str = Field("amber", min_length=1, max_length=64)
    word_range: Optional[List[int]] = None
    tags: Optional[List[str]] = None


class NoteUpdate(BaseModel):
    note: Optional[str] = Field(None, max_length=20000)
    color: Optional[str] = Field(None, min_length=1, max_length=64)
    tags: Optional[List[str]] = None


class NoteResponse(BaseModel):
    id: int
    document_id: int
    page_number: int
    end_page: Optional[int] = None
    quote: Optional[str] = None
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    note: Optional[str] = None
    color: str
    word_range: Optional[List[int]] = None
    tags: List[str] = []
    flashcard_id: Optional[int] = None
    document_title: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class NoteColorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    hex: str = Field(..., min_length=4, max_length=7)


class NoteColorUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    hex: Optional[str] = Field(None, min_length=4, max_length=7)


class NoteColorResponse(BaseModel):
    id: str
    name: str
    hex: str
    built_in: bool
    hint: Optional[str] = None
