"""note tags + flashcards.source_note_id

Revision ID: c9f1d6e28a14
Revises: b7e4c2a19d08
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f1d6e28a14"
down_revision: Union[str, None] = "b7e4c2a19d08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column(
        "flashcards",
        sa.Column("source_note_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_flashcards_source_note_id", "flashcards", ["source_note_id"], unique=False)
    op.create_foreign_key(
        "fk_flashcards_source_note_id",
        "flashcards",
        "notes",
        ["source_note_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_flashcards_source_note_id", "flashcards", type_="foreignkey")
    op.drop_index("ix_flashcards_source_note_id", table_name="flashcards")
    op.drop_column("flashcards", "source_note_id")
    op.drop_column("notes", "tags")
