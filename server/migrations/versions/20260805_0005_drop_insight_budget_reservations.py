"""Persist Drop Insight resource budget reservations and settlements."""

from alembic import op
import sqlalchemy as sa

revision = "20260805_0005"
down_revision = "20260805_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("drop_insight_tool_calls")
    }
    with op.batch_alter_table("drop_insight_tool_calls") as batch:
        if "budget_reservation_json" not in existing:
            batch.add_column(sa.Column("budget_reservation_json", sa.JSON(), nullable=True))
        if "budget_settlement_json" not in existing:
            batch.add_column(sa.Column("budget_settlement_json", sa.JSON(), nullable=True))
        if "budget_reservation_status" not in existing:
            batch.add_column(
                sa.Column(
                    "budget_reservation_status",
                    sa.String(length=32),
                    nullable=False,
                    server_default="NONE",
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("drop_insight_tool_calls")
    }
    with op.batch_alter_table("drop_insight_tool_calls") as batch:
        if "budget_reservation_status" in existing:
            batch.drop_column("budget_reservation_status")
        if "budget_settlement_json" in existing:
            batch.drop_column("budget_settlement_json")
        if "budget_reservation_json" in existing:
            batch.drop_column("budget_reservation_json")
