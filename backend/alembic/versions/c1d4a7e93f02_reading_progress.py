"""reading progress: remember where the reader was left

Revision ID: c1d4a7e93f02
Revises: 108bf27c9c31
Create Date: 2026-08-09

Bookmarks already existed but are a different thing: a bookmark is something
the user deliberately marks and expects to find again. Resuming is passive -
it should cost nothing and require no decision at the moment you stop
reading, which is exactly when you are least likely to press a button.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d4a7e93f02"
down_revision: Union[str, None] = "108bf27c9c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All nullable: every existing document predates any reading history, and
    # "never opened" has to stay distinguishable from "opened at page 1", or
    # the library would claim the user had started every document they own.
    op.add_column("documents", sa.Column("last_read_page", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("last_read_position", sa.Float(), nullable=True))
    op.add_column("documents", sa.Column("last_read_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_documents_last_read_at", "documents", ["last_read_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_documents_last_read_at", table_name="documents")
    op.drop_column("documents", "last_read_at")
    op.drop_column("documents", "last_read_position")
    op.drop_column("documents", "last_read_page")
