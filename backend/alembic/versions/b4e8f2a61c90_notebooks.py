"""Add per-user notebooks and optional document filing."""
from alembic import op
import sqlalchemy as sa


revision = "b4e8f2a61c90"
down_revision = "a6c4b3d18e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notebooks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=240), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index("ix_notebooks_user_id", "notebooks", ["user_id"])
    op.create_index(
        "uq_notebooks_user_name_ci",
        "notebooks",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )
    notebook_column = sa.Column(
        "notebook_id",
        sa.Integer(),
        sa.ForeignKey("notebooks.id", name="fk_documents_notebook_id", ondelete="SET NULL"),
        nullable=True,
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("documents") as batch_op:
            batch_op.add_column(notebook_column)
    else:
        op.add_column("documents", notebook_column)
    op.create_index("ix_documents_notebook_id", "documents", ["notebook_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_notebook_id", table_name="documents")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("documents") as batch_op:
            batch_op.drop_column("notebook_id")
    else:
        op.drop_column("documents", "notebook_id")
    op.drop_index("uq_notebooks_user_name_ci", table_name="notebooks")
    op.drop_index("ix_notebooks_user_id", table_name="notebooks")
    op.drop_table("notebooks")
