"""Add task-level idempotency key for CreateTask replay protection."""

from alembic import op
import sqlalchemy as sa

revision = "20260806_0006"
down_revision = "20260805_0005"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_tasks_creator_idempotency"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("tasks")
    }
    # Use direct ALTER TABLE ADD COLUMN: both columns are nullable without a
    # default, which SQLite supports, and plain op.add_column does not reconcile
    # against model metadata the way batch_alter_table does.
    if "idempotency_key" not in existing:
        op.add_column(
            "tasks", sa.Column("idempotency_key", sa.String(length=128), nullable=True)
        )
    if "creator_id" not in existing:
        op.add_column(
            "tasks", sa.Column("creator_id", sa.String(length=128), nullable=True)
        )
    op.create_index(_INDEX_NAME, "tasks", ["creator_id", "idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="tasks")
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("tasks")
    }
    if "idempotency_key" in existing:
        op.drop_column("tasks", "idempotency_key")
    if "creator_id" in existing:
        op.drop_column("tasks", "creator_id")
