"""notes.end_page for highlights that cross a page

Revision ID: d2a8e41c70b3
Revises: c9f1d6e28a14
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2a8e41c70b3"
down_revision: Union[str, None] = "c9f1d6e28a14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("end_page", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("notes", "end_page")
