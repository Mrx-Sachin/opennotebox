"""notes table for highlights and written notes

Revision ID: b7e4c2a19d08
Revises: f4a2b8c1d3e7
Create Date: 2026-08-15

A note is a highlight (quote, optional body) or a page-level note
(no quote). Color is a palette id - built-in amber/green/indigo or a
user-defined slug stored in user_settings.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4c2a19d08"
down_revision: Union[str, None] = "f4a2b8c1d3e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("context_before", sa.Text(), nullable=True),
        sa.Column("context_after", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("color", sa.String(), nullable=False, server_default="amber"),
        sa.Column("word_range", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_notes_user_id", "notes", ["user_id"], unique=False)
    op.create_index(
        "ix_notes_document_id_page_number",
        "notes",
        ["document_id", "page_number"],
        unique=False,
    )
    op.create_index(
        "ix_notes_user_id_document_id",
        "notes",
        ["user_id", "document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notes_user_id_document_id", table_name="notes")
    op.drop_index("ix_notes_document_id_page_number", table_name="notes")
    op.drop_index("ix_notes_user_id", table_name="notes")
    op.drop_table("notes")
