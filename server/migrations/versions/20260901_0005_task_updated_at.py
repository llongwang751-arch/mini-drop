"""Add the task timestamp required by the native control plane."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260901_0005"
down_revision = "20260901_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "tasks" not in tables:
        return
    columns = {item["name"] for item in inspect(bind).get_columns("tasks")}
    if "updated_at" not in columns:
        op.add_column(
            "tasks",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "tasks" not in tables:
        return
    columns = {item["name"] for item in inspect(bind).get_columns("tasks")}
    if "updated_at" in columns:
        op.drop_column("tasks", "updated_at")
