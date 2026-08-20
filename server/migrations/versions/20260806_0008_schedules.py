"""General schedule/cron: immutable task templates with cron triggers."""

from alembic import op
import sqlalchemy as sa

revision = "20260806_0008"
down_revision = "20260806_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The 0001 baseline seeds every table from the current model metadata, so
    # guard against the tables already existing (see 0007 note).
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "schedules" not in tables:
        op.create_table(
            "schedules",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("name", sa.String(length=256), nullable=False),
            sa.Column("cron_expression", sa.String(length=64), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("task_template_json", sa.JSON(), nullable=False),
            sa.Column(
                "enabled", sa.Boolean(), nullable=False, server_default="1"
            ),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "schedule_records" not in tables:
        op.create_table(
            "schedule_records",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("schedule_id", sa.String(length=128), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("task_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "schedule_id",
                "scheduled_at",
                name="uq_schedule_record_slot",
            ),
        )
    if "schedules" in tables:
        indexes = {
            item["name"] for item in sa.inspect(bind).get_indexes("schedules")
        }
        if "ix_schedules_due" not in indexes:
            op.create_index(
                "ix_schedules_due", "schedules", ["enabled", "next_run_at"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "schedule_records" in tables:
        op.drop_table("schedule_records")
    if "schedules" in tables:
        indexes = {
            item["name"] for item in sa.inspect(bind).get_indexes("schedules")
        }
        if "ix_schedules_due" in indexes:
            op.drop_index("ix_schedules_due", table_name="schedules")
        op.drop_table("schedules")
