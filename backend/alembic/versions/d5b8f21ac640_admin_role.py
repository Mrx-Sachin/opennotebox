"""Add an admin role to users.

The instance had no notion of privilege at all: every account was equal, so
there was no one who could remove a stale or unwanted account, and the only
way to do it was psql. This adds the smallest thing that fixes that - one
boolean - rather than a role table, because there are exactly two levels here
and a join table would be ceremony around a single bit.

Bootstrap: the earliest-created account becomes admin. Someone has to be
first, and on a self-hosted instance that account is the person who installed
it. Doing it in the migration means the privilege exists the moment the column
does, with no window where the feature is deployed but unreachable and no
"promote yourself" endpoint left lying around afterwards.

Revision ID: d5b8f21ac640
Revises: c1d4a7e93f02
"""
from alembic import op
import sqlalchemy as sa

revision = "d5b8f21ac640"
down_revision = "c1d4a7e93f02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default so existing rows get a real value instead of NULL, and
    # nullable=False so "unknown privilege" can never be a state - a NULL here
    # would be read as falsy by Python and as unknown by SQL, which is exactly
    # the kind of split no authorisation check should have to think about.
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE users SET is_admin = true
        WHERE id = (SELECT id FROM users ORDER BY created_at ASC, id ASC LIMIT 1)
        """
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
