"""knowledge graph tables

Revision ID: f4a2b8c1d3e7
Revises: e7c3a91b4d20
Create Date: 2024-08-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a2b8c1d3e7'
down_revision: Union[str, None] = 'e7c3a91b4d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kg_entities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False, server_default="other"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("pages", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("document_id", "name_normalized", name="uq_kg_entity_doc_name"),
        sa.Index("ix_kg_entities_document_id", "document_id"),
    )

    op.create_table(
        "kg_extraction_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("chunks_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("document_id", name="uq_kg_extraction_doc"),
    )

    op.create_table(
        "kg_relationships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_id", sa.Integer(), sa.ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.UniqueConstraint("subject_id", "relationship", "object_id", name="uq_kg_rel"),
        sa.Index("ix_kg_relationships_subject_id", "subject_id"),
        sa.Index("ix_kg_relationships_object_id", "object_id"),
        sa.Index("ix_kg_relationships_document_id", "document_id"),
    )


def downgrade() -> None:
    op.drop_table("kg_relationships")
    op.drop_table("kg_extraction_state")
    op.drop_table("kg_entities")
