"""Persist the per-diagnosis Skill policy used by live A/B runs."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260901_0006"
down_revision = "20260901_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "drop_insight_sessions" not in tables:
        return
    columns = {
        item["name"] for item in inspect(bind).get_columns("drop_insight_sessions")
    }
    if "skill_policy" not in columns:
        op.add_column(
            "drop_insight_sessions",
            sa.Column(
                "skill_policy",
                sa.String(length=16),
                nullable=False,
                server_default="AUTO",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "drop_insight_sessions" not in tables:
        return
    columns = {
        item["name"] for item in inspect(bind).get_columns("drop_insight_sessions")
    }
    if "skill_policy" in columns:
        op.drop_column("drop_insight_sessions", "skill_policy")
