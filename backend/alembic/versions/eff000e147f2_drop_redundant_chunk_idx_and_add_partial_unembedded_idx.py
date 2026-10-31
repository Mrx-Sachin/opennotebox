"""Drop redundant idx_chunk_doc and add partial unembedded chunk index.

Revision ID: eff000e147f2
Revises: e3d7a8b9c2f1
Create Date: 2026-10-30 12:20:32.433112
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "eff000e147f2"
down_revision: Union[str, None] = "e3d7a8b9c2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("chunk"):
        return

    existing_indexes = {
        index_data["name"] for index_data in inspector.get_indexes("chunk")
    }
    if "idx_chunk_doc" in existing_indexes:
        op.drop_index("idx_chunk_doc", table_name="chunk", if_exists=True)

    if "idx_chunk_unembedded" not in existing_indexes:
        op.create_index(
            "idx_chunk_unembedded",
            "chunk",
            ["document_id", "page_number", "chunk_index"],
            postgresql_where=sa.text("emb IS NULL"),
            sqlite_where=sa.text("emb IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("chunk"):
        return

    existing_indexes = {
        index_data["name"] for index_data in inspector.get_indexes("chunk")
    }
    if "idx_chunk_unembedded" in existing_indexes:
        op.drop_index(
            "idx_chunk_unembedded", table_name="chunk", if_exists=True
        )

    if "idx_chunk_doc" not in existing_indexes:
        op.create_index(
            "idx_chunk_doc",
            "chunk",
            ["document_id", "page_number", "chunk_index"],
        )
