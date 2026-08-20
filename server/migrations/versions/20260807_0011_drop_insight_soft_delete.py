"""Drop Insight 会话软归档：列表隐藏但保留证据与审计可追溯。"""

from alembic import op
import sqlalchemy as sa

revision = "20260807_0011"
down_revision = "20260806_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "drop_insight_sessions" not in tables:
        return
    columns = {
        item["name"] for item in sa.inspect(bind).get_columns("drop_insight_sessions")
    }
    if "deleted_at" not in columns:
        op.add_column("drop_insight_sessions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    if "deleted_by" not in columns:
        op.add_column("drop_insight_sessions", sa.Column("deleted_by", sa.String(length=128), nullable=True))
    if "delete_reason" not in columns:
        op.add_column("drop_insight_sessions", sa.Column("delete_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "drop_insight_sessions" not in tables:
        return
    columns = {
        item["name"] for item in sa.inspect(bind).get_columns("drop_insight_sessions")
    }
    if "delete_reason" in columns:
        op.drop_column("drop_insight_sessions", "delete_reason")
    if "deleted_by" in columns:
        op.drop_column("drop_insight_sessions", "deleted_by")
    if "deleted_at" in columns:
        op.drop_column("drop_insight_sessions", "deleted_at")
