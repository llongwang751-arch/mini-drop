"""Persist diagnosis-scoped conversational Runtime state."""

from alembic import op
import sqlalchemy as sa

revision = "20260823_0019"
down_revision = "20260822_0018"
branch_labels = None
depends_on = None


def _require_diagnosis_sessions(bind) -> None:
    if "diagnosis_sessions" not in set(sa.inspect(bind).get_table_names()):
        raise RuntimeError("cannot create Agent Runtime tables: diagnosis_sessions is missing")


def upgrade() -> None:
    bind = op.get_bind()
    _require_diagnosis_sessions(bind)
    tables = set(sa.inspect(bind).get_table_names())

    if "agent_runtime_bindings" not in tables:
        op.create_table(
            "agent_runtime_bindings",
            sa.Column("diagnosis_id", sa.String(128), primary_key=True),
            sa.Column("runtime_type", sa.String(32), nullable=False),
            sa.Column("runtime_version", sa.String(64), nullable=False),
            sa.Column("runtime_session_id", sa.String(128), nullable=False),
            sa.Column("runtime_generation", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
            sa.Column("last_event_seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_context_snapshot_id", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
        )

    if "agent_runtime_turns" not in tables:
        op.create_table(
            "agent_runtime_turns",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("diagnosis_id", sa.String(128), nullable=False),
            sa.Column("turn_id", sa.String(128), nullable=False),
            sa.Column("runtime_session_id", sa.String(128), nullable=True),
            sa.Column("runtime_generation", sa.Integer(), nullable=False),
            sa.Column("user_message", sa.Text(), nullable=False),
            sa.Column("requested_mode", sa.String(40), nullable=True),
            sa.Column("side_effect_policy", sa.String(24), nullable=True),
            sa.Column("actor_id", sa.String(128), nullable=True),
            sa.Column("client_command_id", sa.String(128), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="SUBMITTING"),
            sa.Column("accepted_mode", sa.String(32), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("final_message_json", sa.JSON(), nullable=True),
            sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
            sa.UniqueConstraint(
                "diagnosis_id", "client_command_id",
                name="uq_agent_runtime_turn_command",
            ),
            sa.UniqueConstraint(
                "diagnosis_id", "turn_id",
                name="uq_agent_runtime_turn_identity",
            ),
        )
        op.create_index(
            "ix_agent_runtime_turns_diagnosis_id",
            "agent_runtime_turns",
            ["diagnosis_id"],
        )

    if "agent_runtime_events" not in tables:
        op.create_table(
            "agent_runtime_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_id", sa.String(128), nullable=False),
            sa.Column("diagnosis_id", sa.String(128), nullable=False),
            sa.Column("turn_id", sa.String(128), nullable=False),
            sa.Column("runtime_generation", sa.Integer(), nullable=False),
            sa.Column("event_seq", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
            sa.ForeignKeyConstraint(
                ["diagnosis_id", "turn_id"],
                ["agent_runtime_turns.diagnosis_id", "agent_runtime_turns.turn_id"],
                name="fk_agent_runtime_events_turn",
            ),
            sa.UniqueConstraint(
                "diagnosis_id", "turn_id", "runtime_generation", "event_seq",
                name="uq_agent_runtime_event_sequence",
            ),
            sa.UniqueConstraint(
                "diagnosis_id", "event_id",
                name="uq_agent_runtime_event_identity",
            ),
        )
        op.create_index(
            "ix_agent_runtime_events_diagnosis_id",
            "agent_runtime_events",
            ["diagnosis_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table in (
        "agent_runtime_events",
        "agent_runtime_turns",
        "agent_runtime_bindings",
    ):
        if table in tables:
            op.drop_table(table)
