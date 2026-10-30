"""Add layout_regions and narration_exclusions to extracted_text table."""

from alembic import op
import sqlalchemy as sa

revision = "e3d7a8b9c2f1"
down_revision = "2c8a9f4e1b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "extracted_text" in tables:
        columns = [c["name"] for c in inspector.get_columns("extracted_text")]
        if "layout_regions" not in columns:
            op.add_column(
                "extracted_text",
                sa.Column("layout_regions", sa.JSON(), nullable=True),
            )
        if "narration_exclusions" not in columns:
            op.add_column(
                "extracted_text",
                sa.Column("narration_exclusions", sa.JSON(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "extracted_text" in tables:
        columns = [c["name"] for c in inspector.get_columns("extracted_text")]
        if "narration_exclusions" in columns:
            op.drop_column("extracted_text", "narration_exclusions")
        if "layout_regions" in columns:
            op.drop_column("extracted_text", "layout_regions")
