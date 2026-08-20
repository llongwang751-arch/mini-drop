"""Composite task / DAG: aggregate child task outcomes with a strategy."""

from alembic import op
import sqlalchemy as sa

revision = "20260806_0009"
down_revision = "20260806_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The 0001 baseline seeds every table from the current model metadata, so
    # guard against the tables already existing (see 0007 note).
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "composite_tasks" not in tables:
        op.create_table(
            "composite_tasks",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("name", sa.String(length=256), nullable=False),
            sa.Column("strategy", sa.String(length=32), nullable=False),
            sa.Column("required_success_count", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "composite_task_items" not in tables:
        op.create_table(
            "composite_task_items",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("composite_id", sa.String(length=128), nullable=False),
            sa.Column("task_id", sa.String(length=128), nullable=True),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "composite_tasks" in tables:
        indexes = {
            item["name"] for item in sa.inspect(bind).get_indexes("composite_tasks")
        }
        if "ix_composite_status" not in indexes:
            op.create_index("ix_composite_status", "composite_tasks", ["status"])
    if "composite_task_items" in tables:
        indexes = {
            item["name"]
            for item in sa.inspect(bind).get_indexes("composite_task_items")
        }
        if "ix_composite_item_composite" not in indexes:
            op.create_index(
                "ix_composite_item_composite", "composite_task_items", ["composite_id"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "composite_task_items" in tables:
        op.drop_table("composite_task_items")
    if "composite_tasks" in tables:
        op.drop_index("ix_composite_status", table_name="composite_tasks")
        op.drop_table("composite_tasks")
