"""Track optional layout analysis separately from text extraction."""

from alembic import op
import sqlalchemy as sa

revision = "a91c2e7d408b"
down_revision = "eff000e147f2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("layout_status", sa.String(32), nullable=False, server_default="not_requested"))
    op.add_column("documents", sa.Column("layout_task_id", sa.String(36), nullable=True))
    op.add_column("documents", sa.Column("layout_error", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("layout_requested_at", sa.DateTime(timezone=True), nullable=True))
    # Empty arrays are a valid successful legacy analysis. SQL and JSON null
    # both mean the older extraction never persisted enrichment for that page.
    op.execute(sa.text("""
        UPDATE documents SET layout_status = 'ready'
        WHERE format = 'pdf' AND num_pages > 0
          AND num_pages = (SELECT COUNT(*) FROM extracted_text WHERE document_id = documents.id)
          AND NOT EXISTS (
            SELECT 1 FROM extracted_text WHERE document_id = documents.id
            AND (layout_regions IS NULL OR CAST(layout_regions AS TEXT) = 'null'
                 OR narration_exclusions IS NULL OR CAST(narration_exclusions AS TEXT) = 'null')
          )
    """))


def downgrade():
    op.drop_column("documents", "layout_requested_at")
    op.drop_column("documents", "layout_error")
    op.drop_column("documents", "layout_task_id")
    op.drop_column("documents", "layout_status")
