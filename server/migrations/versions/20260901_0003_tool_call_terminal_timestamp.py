"""Add the durable terminal-processing timestamp to legacy tool calls."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260901_0003"
down_revision = "20260901_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "drop_insight_tool_calls" not in tables:
        return
    columns = {
        item["name"] for item in inspect(bind).get_columns("drop_insight_tool_calls")
    }
    if "terminal_processed_at" not in columns:
        op.add_column(
            "drop_insight_tool_calls",
            sa.Column("terminal_processed_at", sa.DateTime(timezone=True)),
        )


def downgrade() -> None:
    pass
