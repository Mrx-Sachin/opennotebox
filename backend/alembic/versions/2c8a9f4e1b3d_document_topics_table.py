"""Create document_topics table and shared catalog index if missing."""
from alembic import op
import sqlalchemy as sa


revision = "2c8a9f4e1b3d"
down_revision = "f8d3c6a17b42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "document_topics" not in tables:
        op.create_table(
            "document_topics",
            sa.Column(
                "document_id",
                sa.Integer(),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("topic", sa.String(length=40), primary_key=True),
        )
        inspector.clear_cache()

    topic_indexes = [
        index_data["name"]
        for index_data in inspector.get_indexes("document_topics")
    ]
    if "ix_document_topics_topic_document" not in topic_indexes:
        op.create_index(
            "ix_document_topics_topic_document",
            "document_topics",
            ["topic", "document_id"],
        )

    if "documents" in tables:
        document_indexes = [
            index_data["name"]
            for index_data in inspector.get_indexes("documents")
        ]
        if "ix_documents_shared_catalog" not in document_indexes:
            op.create_index(
                "ix_documents_shared_catalog",
                "documents",
                ["is_shared", "shared_at", "id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "document_topics" in tables:
        topic_indexes = [
            index_data["name"]
            for index_data in inspector.get_indexes("document_topics")
        ]
        if "ix_document_topics_topic_document" in topic_indexes:
            op.drop_index(
                "ix_document_topics_topic_document",
                table_name="document_topics",
            )
        op.drop_table("document_topics")
        inspector.clear_cache()

    if "documents" in tables:
        document_indexes = [
            index_data["name"]
            for index_data in inspector.get_indexes("documents")
        ]
        if "ix_documents_shared_catalog" in document_indexes:
            op.drop_index(
                "ix_documents_shared_catalog",
                table_name="documents",
            )
