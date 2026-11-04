"""Add hint column to flashcards table."""

from alembic import op
import sqlalchemy as sa

revision = "f5b7c8d9e012"
down_revision = "a91c2e7d408b"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("flashcards", sa.Column("hint", sa.Text(), nullable=True))

def downgrade():
    op.drop_column("flashcards", "hint")
