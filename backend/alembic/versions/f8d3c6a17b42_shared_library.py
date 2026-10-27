"""Add reusable assets, per-user library membership, and shared metadata."""
from alembic import op
import sqlalchemy as sa


revision = "f8d3c6a17b42"
down_revision = "b4e8f2a61c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "library_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "notebook_id",
            sa.Integer(),
            sa.ForeignKey("notebooks.id", name="fk_library_entries_notebook_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("added_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=True),
        sa.Column("can_publish", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_read_page", sa.Integer(), nullable=True),
        sa.Column("last_read_position", sa.Float(), nullable=True),
        sa.Column("last_read_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "document_id", name="uq_library_entries_user_document"),
    )
    op.create_index("ix_library_entries_user_id", "library_entries", ["user_id"])
    op.create_index("ix_library_entries_document_id", "library_entries", ["document_id"])
    op.create_index("ix_library_entries_notebook_id", "library_entries", ["notebook_id"])
    op.create_index("ix_library_entries_last_read_at", "library_entries", ["last_read_at"])
    op.create_index("ix_library_entries_user_notebook", "library_entries", ["user_id", "notebook_id"])
    op.create_index(
        "ix_library_entries_user_added",
        "library_entries",
        ["user_id", "added_at", "document_id"],
    )

    for column in (
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("shared_at", sa.DateTime(), nullable=True),
        sa.Column("shared_title", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("topics", sa.JSON(), nullable=True),
        sa.Column("document_type", sa.String(length=80), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
    ):
        op.add_column("documents", column)
    shared_by_column = sa.Column(
        "shared_by_user_id",
        sa.Integer(),
        sa.ForeignKey("users.id", name="fk_documents_shared_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("documents") as batch_op:
            batch_op.add_column(shared_by_column)
    else:
        op.add_column("documents", shared_by_column)
    op.create_index("ix_documents_shared_by_user_id", "documents", ["shared_by_user_id"])
    op.create_index("uq_documents_content_hash", "documents", ["content_hash"], unique=True)
    op.create_index(
        "ix_documents_shared_catalog", "documents", ["is_shared", "shared_at", "id"]
    )

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
    op.create_index(
        "ix_document_topics_topic_document",
        "document_topics",
        ["topic", "document_id"],
    )

    bind = op.get_bind()
    bind.execute(sa.text(
        """INSERT INTO library_entries
            (user_id, document_id, notebook_id, added_at, title, original_url,
             can_publish, last_read_page, last_read_position, last_read_at)
        SELECT user_id, id, notebook_id, created_at, title, original_url,
               TRUE, last_read_page, last_read_position, last_read_at
        FROM documents WHERE user_id IS NOT NULL"""
    ))

    summary_user_column = sa.Column(
        "user_id",
        sa.Integer(),
        sa.ForeignKey("users.id", name="fk_summaries_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("summaries") as batch_op:
            batch_op.add_column(summary_user_column)
    else:
        op.add_column("summaries", summary_user_column)
    op.create_index("ix_summaries_user_id", "summaries", ["user_id"])
    bind.execute(sa.text(
        """UPDATE summaries SET user_id = (
            SELECT documents.user_id FROM documents
            WHERE documents.id = summaries.document_id
        ) WHERE user_id IS NULL"""
    ))

    naming_convention = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    }
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("documents", naming_convention=naming_convention) as batch_op:
            batch_op.drop_constraint("fk_documents_user_id_users", type_="foreignkey")
            batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
            batch_op.create_foreign_key(
                "fk_documents_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
            )
    else:
        inspector = sa.inspect(bind)
        for foreign_key in inspector.get_foreign_keys("documents"):
            if foreign_key.get("constrained_columns") == ["user_id"]:
                if foreign_key.get("name"):
                    op.drop_constraint(foreign_key["name"], "documents", type_="foreignkey")
                break
        op.alter_column("documents", "user_id", existing_type=sa.Integer(), nullable=True)
        op.create_foreign_key(
            "fk_documents_user_id", "documents", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    unsupported_shared = bind.execute(
        sa.text("SELECT count(*) FROM documents WHERE is_shared = TRUE")
    ).scalar_one()
    unsupported_membership = bind.execute(sa.text(
        """SELECT count(*) FROM documents
        WHERE user_id IS NULL
           OR (SELECT count(*) FROM library_entries WHERE document_id = documents.id) != 1
           OR NOT EXISTS (
                SELECT 1 FROM library_entries
                WHERE library_entries.document_id = documents.id
                  AND library_entries.user_id = documents.user_id
           )"""
    )).scalar_one()
    unsupported_summaries = bind.execute(sa.text(
        """SELECT count(*) FROM summaries
        WHERE user_id IS NOT NULL AND user_id != (
            SELECT user_id FROM documents WHERE documents.id = summaries.document_id
        )"""
    )).scalar_one()
    if unsupported_shared or unsupported_membership or unsupported_summaries:
        raise RuntimeError(
            "Cannot downgrade shared-library data while shared or multi-user assets exist"
        )

    bind.execute(sa.text(
        """UPDATE documents SET
            notebook_id = (SELECT notebook_id FROM library_entries WHERE document_id = documents.id),
            title = (SELECT title FROM library_entries WHERE document_id = documents.id),
            original_url = (SELECT original_url FROM library_entries WHERE document_id = documents.id),
            last_read_page = (SELECT last_read_page FROM library_entries WHERE document_id = documents.id),
            last_read_position = (SELECT last_read_position FROM library_entries WHERE document_id = documents.id),
            last_read_at = (SELECT last_read_at FROM library_entries WHERE document_id = documents.id)
        WHERE user_id IS NOT NULL"""
    ))

    if bind.dialect.name == "sqlite":
        naming_convention = {
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
        }
        with op.batch_alter_table("documents", naming_convention=naming_convention) as batch_op:
            batch_op.drop_constraint("fk_documents_user_id", type_="foreignkey")
            batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(
                "fk_documents_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
            )
    else:
        op.drop_constraint("fk_documents_user_id", "documents", type_="foreignkey")
        op.alter_column("documents", "user_id", existing_type=sa.Integer(), nullable=False)
        op.create_foreign_key(
            "fk_documents_user_id", "documents", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    op.drop_index("ix_summaries_user_id", table_name="summaries")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("summaries") as batch_op:
            batch_op.drop_constraint("fk_summaries_user_id", type_="foreignkey")
            batch_op.drop_column("user_id")
    else:
        op.drop_constraint("fk_summaries_user_id", "summaries", type_="foreignkey")
        op.drop_column("summaries", "user_id")
    op.drop_index("uq_documents_content_hash", table_name="documents")
    op.drop_index("ix_documents_shared_catalog", table_name="documents")
    op.drop_index("ix_documents_shared_by_user_id", table_name="documents")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("documents") as batch_op:
            batch_op.drop_constraint("fk_documents_shared_by_user_id", type_="foreignkey")
            batch_op.drop_column("shared_by_user_id")
    else:
        op.drop_constraint("fk_documents_shared_by_user_id", "documents", type_="foreignkey")
        op.drop_column("documents", "shared_by_user_id")
    for column in (
        "language",
        "document_type",
        "topics",
        "description",
        "author",
        "shared_title",
        "shared_at",
        "is_shared",
        "content_hash",
    ):
        op.drop_column("documents", column)
    op.drop_index("ix_document_topics_topic_document", table_name="document_topics")
    op.drop_table("document_topics")
    for index in (
        "ix_library_entries_user_notebook",
        "ix_library_entries_user_added",
        "ix_library_entries_last_read_at",
        "ix_library_entries_notebook_id",
        "ix_library_entries_document_id",
        "ix_library_entries_user_id",
    ):
        op.drop_index(index, table_name="library_entries")
    op.drop_table("library_entries")
