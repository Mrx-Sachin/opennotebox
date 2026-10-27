"""Composite indexes for hot document/summary/flashcard queries.

extracted_text and summaries are almost always filtered by document_id plus
a second key (page_number or mode). Single-column indexes still force a
filter on the second key after the index probe. Flashcard due-queue and
reps lookups are the same shape for (user_id, due) / (user_id, reps).

Revision ID: e7c3a91b4d20
Revises: d5b8f21ac640
"""
from alembic import op

revision = "e7c3a91b4d20"
down_revision = "d5b8f21ac640"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_extracted_text_document_id_page_number",
        "extracted_text",
        ["document_id", "page_number"],
    )
    op.create_index(
        "ix_summaries_document_id_mode",
        "summaries",
        ["document_id", "mode"],
    )
    op.create_index(
        "ix_flashcards_user_id_due",
        "flashcards",
        ["user_id", "due"],
    )
    op.create_index(
        "ix_flashcards_user_id_reps",
        "flashcards",
        ["user_id", "reps"],
    )


def downgrade() -> None:
    op.drop_index("ix_flashcards_user_id_reps", table_name="flashcards")
    op.drop_index("ix_flashcards_user_id_due", table_name="flashcards")
    op.drop_index("ix_summaries_document_id_mode", table_name="summaries")
    op.drop_index(
        "ix_extracted_text_document_id_page_number",
        table_name="extracted_text",
    )
