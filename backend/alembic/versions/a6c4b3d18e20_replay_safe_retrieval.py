"""Add extraction uniqueness, durable embedding jobs, and vector retrieval.

Legacy duplicate extracted pages keep the newest row. For duplicate raw
retrieval chunks, keep the newest row with an embedding when one exists. This
preserves the latest extracted content and the most useful generated vector.
The legacy BYTEA embedding column remains in place as a compatibility copy.
"""
from __future__ import annotations

import struct

import sqlalchemy as sa
from alembic import op

revision = "a6c4b3d18e20"
down_revision = "d2a8e41c70b3"
branch_labels = None
depends_on = None

VECTOR_DIMENSIONS = 768


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _add_vector_column_and_backfill() -> None:
    bind = op.get_bind()
    extension_available = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_available_extensions "
            "WHERE name = 'vector')"
        )
    )
    if not extension_available:
        return

    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_file THEN
                RAISE NOTICE 'pgvector is available but could not be enabled';
        END
        $$
        """
    )
    extension_installed = bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    )
    if not extension_installed:
        return
    op.execute(
        "ALTER TABLE chunk ADD COLUMN IF NOT EXISTS embedding vector(768)"
    )

    last_id = 0
    while True:
        rows = bind.execute(
            sa.text(
                "SELECT id, emb FROM chunk "
                "WHERE id > :last_id AND emb IS NOT NULL AND embedding IS NULL "
                "ORDER BY id LIMIT 500"
            ),
            {"last_id": last_id},
        ).all()
        if not rows:
            break

        updates = []
        for chunk_id, blob in rows:
            last_id = chunk_id
            raw = bytes(blob)
            if len(raw) % 4 or len(raw) // 4 != VECTOR_DIMENSIONS:
                continue
            vector = struct.unpack(f"{len(raw) // 4}f", raw)
            vector_literal = "[" + ",".join(
                format(value, ".9g") for value in vector
            ) + "]"
            updates.append({"chunk_id": chunk_id, "vector": vector_literal})
        if updates:
            bind.execute(
                sa.text(
                    "UPDATE chunk SET embedding = CAST(:vector AS vector) "
                    "WHERE id = :chunk_id"
                ),
                updates,
            )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_embedding_hnsw "
        "ON chunk USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL"
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "DELETE FROM extracted_text a USING extracted_text b "
            "WHERE a.document_id = b.document_id "
            "AND a.page_number = b.page_number AND a.id < b.id"
        )
        op.execute(
            "DROP INDEX IF EXISTS ix_extracted_text_document_id_page_number"
        )
        op.create_unique_constraint(
            "uq_extracted_text_document_page",
            "extracted_text",
            ["document_id", "page_number"],
        )
    else:
        op.execute(
            "DELETE FROM extracted_text WHERE id NOT IN ("
            "SELECT MAX(id) FROM extracted_text GROUP BY document_id, page_number)"
        )
        op.drop_index(
            "ix_extracted_text_document_id_page_number",
            table_name="extracted_text",
            if_exists=True,
        )
        with op.batch_alter_table("extracted_text") as batch_op:
            batch_op.create_unique_constraint(
                "uq_extracted_text_document_page",
                ["document_id", "page_number"],
            )

    op.create_table(
        "embedding_backfills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("document_id", name="uq_embedding_backfills_document"),
    )
    op.create_index(
        "ix_embedding_backfills_status", "embedding_backfills", ["status"]
    )

    if dialect == "postgresql":
        if not _has_table("chunk"):
            op.execute(
                """
                CREATE TABLE chunk (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER NOT NULL,
                    page_number INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    tsvectors TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('english', COALESCE(text, ''))
                    ) STORED,
                    emb BYTEA
                )
                """
            )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunk_doc "
            "ON chunk(document_id, page_number, chunk_index)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunk_fts ON chunk USING GIN(tsvectors)"
        )
        # Retain one copy of legacy repeated rows. A populated BYTEA embedding
        # wins so the migration does not discard generated retrieval artifacts.
        op.execute(
            """
            DELETE FROM chunk c USING (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY document_id, page_number, chunk_index
                        ORDER BY (emb IS NOT NULL) DESC, id DESC
                    ) AS row_number
                    FROM chunk
                ) ranked
                WHERE row_number > 1
            ) duplicates
            WHERE c.id = duplicates.id
            """
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_chunk_document_page_index "
            "ON chunk(document_id, page_number, chunk_index)"
        )
        _add_vector_column_and_backfill()
    elif dialect == "sqlite":
        chunk_existed = _has_table("chunk")
        if not chunk_existed:
            op.create_table(
                "chunk",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("document_id", sa.Integer(), nullable=False),
                sa.Column("page_number", sa.Integer(), nullable=False),
                sa.Column("chunk_index", sa.Integer(), nullable=False),
                sa.Column("text", sa.Text(), nullable=False),
                sa.Column("emb", sa.LargeBinary(), nullable=True),
                sa.UniqueConstraint(
                    "document_id", "page_number", "chunk_index",
                    name="uq_chunk_document_page_index",
                ),
            )
        op.execute(
            "DELETE FROM chunk WHERE id NOT IN ("
            "SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
            "PARTITION BY document_id, page_number, chunk_index "
            "ORDER BY (emb IS NOT NULL) DESC, id DESC) AS row_number "
            "FROM chunk) ranked WHERE row_number = 1)"
        )
        if chunk_existed:
            op.create_index(
                "uq_chunk_document_page_index",
                "chunk",
                ["document_id", "page_number", "chunk_index"],
                unique=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table("chunk"):
        if bind.dialect.name == "postgresql":
            op.execute("DROP INDEX IF EXISTS idx_chunk_embedding_hnsw")
            if "embedding" in {c["name"] for c in sa.inspect(bind).get_columns("chunk")}:
                op.drop_column("chunk", "embedding")
            op.execute("DROP INDEX IF EXISTS uq_chunk_document_page_index")
        elif bind.dialect.name == "sqlite":
            index_names = {
                index["name"] for index in sa.inspect(bind).get_indexes("chunk")
            }
            if "uq_chunk_document_page_index" in index_names:
                op.drop_index("uq_chunk_document_page_index", table_name="chunk")

    op.drop_index("ix_embedding_backfills_status", table_name="embedding_backfills")
    op.drop_table("embedding_backfills")

    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "uq_extracted_text_document_page",
            "extracted_text",
            type_="unique",
        )
    else:
        with op.batch_alter_table("extracted_text") as batch_op:
            batch_op.drop_constraint(
                "uq_extracted_text_document_page", type_="unique"
            )
        op.create_index(
            "ix_extracted_text_document_id_page_number",
            "extracted_text",
            ["document_id", "page_number"],
        )
