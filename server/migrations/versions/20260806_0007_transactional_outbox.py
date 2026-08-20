"""General transactional outbox for Task/Event/Dispatch publication."""

from alembic import op
import sqlalchemy as sa

revision = "20260806_0007"
down_revision = "20260806_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The 0001 baseline seeds every table in the CURRENT model metadata via
    # Base.metadata.create_all(checkfirst=True), so outbox_messages may already
    # exist by the time this migration runs. Guard both table and indexes.
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "outbox_messages" not in tables:
        op.create_table(
            "outbox_messages",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("aggregate_type", sa.String(length=64), nullable=False),
            sa.Column("aggregate_id", sa.String(length=128), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("worker_lease_owner", sa.String(length=128), nullable=True),
            sa.Column(
                "worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("outbox_messages")}
    if "ix_outbox_claim" not in indexes:
        op.create_index(
            "ix_outbox_claim",
            "outbox_messages",
            ["status", "next_attempt_at"],
        )
    if "ix_outbox_aggregate" not in indexes:
        op.create_index(
            "ix_outbox_aggregate",
            "outbox_messages",
            ["aggregate_type", "aggregate_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "outbox_messages" not in tables:
        return
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("outbox_messages")}
    if "ix_outbox_aggregate" in indexes:
        op.drop_index("ix_outbox_aggregate", table_name="outbox_messages")
    if "ix_outbox_claim" in indexes:
        op.drop_index("ix_outbox_claim", table_name="outbox_messages")
    op.drop_table("outbox_messages")
