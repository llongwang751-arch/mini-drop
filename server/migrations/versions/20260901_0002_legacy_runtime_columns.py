"""Adopt runtime lineage and effect columns missing from legacy databases."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.engine import Connection


revision = "20260901_0002"
down_revision = "20260901_0001"
branch_labels = None
depends_on = None


def _add_column(bind: Connection, table_name: str, column: sa.Column) -> bool:
    columns = {item["name"] for item in inspect(bind).get_columns(table_name)}
    if column.name in columns:
        return False
    op.add_column(table_name, column)
    return True


def _has_index(bind: Connection, table_name: str, index_name: str) -> bool:
    return any(
        item.get("name") == index_name
        for item in inspect(bind).get_indexes(table_name)
    )


def _has_unique(bind: Connection, table_name: str, columns: list[str]) -> bool:
    expected = list(columns)
    return any(
        item.get("column_names") == expected
        for item in inspect(bind).get_unique_constraints(table_name)
    )


def _has_foreign_key(
    bind: Connection,
    table_name: str,
    columns: list[str],
    referred_table: str,
) -> bool:
    return any(
        item.get("constrained_columns") == columns
        and item.get("referred_table") == referred_table
        for item in inspect(bind).get_foreign_keys(table_name)
    )


def _has_check(bind: Connection, table_name: str, constraint_name: str) -> bool:
    return any(
        item.get("name") == constraint_name
        for item in inspect(bind).get_check_constraints(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "tasks" in tables:
        _add_column(bind, "tasks", sa.Column("error_code", sa.String(64)))
        _add_column(bind, "tasks", sa.Column("error_message", sa.Text()))
        _add_column(
            bind, "tasks", sa.Column("process_snapshot_id", sa.String(128))
        )
        _add_column(bind, "tasks", sa.Column("process_binding_json", sa.JSON()))
        if not _has_index(bind, "tasks", "ix_tasks_error_code"):
            op.create_index("ix_tasks_error_code", "tasks", ["error_code"])
        if not _has_index(bind, "tasks", "ix_tasks_process_snapshot_id"):
            op.create_index(
                "ix_tasks_process_snapshot_id", "tasks", ["process_snapshot_id"]
            )
        if (
            "process_candidate_snapshots" in tables
            and not _has_foreign_key(
                bind, "tasks", ["process_snapshot_id"], "process_candidate_snapshots"
            )
        ):
            op.create_foreign_key(
                "fk_tasks_process_snapshot",
                "tasks",
                "process_candidate_snapshots",
                ["process_snapshot_id"],
                ["id"],
            )

    if "task_attempts" in tables:
        _add_column(
            bind,
            "task_attempts",
            sa.Column("task_attempt_authority_sha256", sa.String(64)),
        )
        if not _has_unique(
            bind, "task_attempts", ["task_attempt_authority_sha256"]
        ):
            op.create_unique_constraint(
                "uq_task_attempt_authority_sha256",
                "task_attempts",
                ["task_attempt_authority_sha256"],
            )

    if "drop_insight_sessions" in tables:
        _add_column(
            bind, "drop_insight_sessions", sa.Column("requested_time_range_json", sa.JSON())
        )
        _add_column(
            bind, "drop_insight_sessions", sa.Column("effective_time_range_json", sa.JSON())
        )

    if "drop_insight_events" in tables:
        _add_column(
            bind, "drop_insight_events", sa.Column("effect_key", sa.String(160))
        )
        if not _has_unique(
            bind, "drop_insight_events", ["diagnosis_id", "effect_key"]
        ):
            op.create_unique_constraint(
                "uq_drop_insight_event_effect",
                "drop_insight_events",
                ["diagnosis_id", "effect_key"],
            )

    if "drop_insight_hypotheses" in tables:
        _add_column(
            bind, "drop_insight_hypotheses", sa.Column("effect_key", sa.String(160))
        )
        if not _has_unique(
            bind, "drop_insight_hypotheses", ["diagnosis_id", "effect_key"]
        ):
            op.create_unique_constraint(
                "uq_drop_insight_hypothesis_effect",
                "drop_insight_hypotheses",
                ["diagnosis_id", "effect_key"],
            )

    if "drop_insight_tool_calls" in tables:
        terminal_added = _add_column(
            bind,
            "drop_insight_tool_calls",
            sa.Column(
                "terminal_processing_status",
                sa.String(32),
                nullable=False,
                server_default="NONE",
            ),
        )
        _add_column(
            bind, "drop_insight_tool_calls", sa.Column("effect_key", sa.String(160))
        )
        if terminal_added:
            op.alter_column(
                "drop_insight_tool_calls",
                "terminal_processing_status",
                server_default=None,
            )
        if not _has_unique(
            bind, "drop_insight_tool_calls", ["diagnosis_id", "effect_key"]
        ):
            op.create_unique_constraint(
                "uq_drop_insight_tool_call_effect",
                "drop_insight_tool_calls",
                ["diagnosis_id", "effect_key"],
            )

    if "drop_insight_reports" in tables:
        status_added = _add_column(
            bind,
            "drop_insight_reports",
            sa.Column(
                "effects_status",
                sa.String(32),
                nullable=False,
                server_default="PENDING",
            ),
        )
        _add_column(
            bind, "drop_insight_reports", sa.Column("effects_phase", sa.String(32))
        )
        _add_column(
            bind, "drop_insight_reports", sa.Column("effects_owner", sa.String(128))
        )
        _add_column(
            bind,
            "drop_insight_reports",
            sa.Column("effects_lease_expires_at", sa.DateTime(timezone=True)),
        )
        fencing_added = _add_column(
            bind,
            "drop_insight_reports",
            sa.Column(
                "effects_fencing_token",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
        _add_column(
            bind,
            "drop_insight_reports",
            sa.Column("effects_applied_at", sa.DateTime(timezone=True)),
        )
        if status_added:
            op.alter_column(
                "drop_insight_reports", "effects_status", server_default=None
            )
        if fencing_added:
            op.alter_column(
                "drop_insight_reports", "effects_fencing_token", server_default=None
            )
        if not _has_check(
            bind, "drop_insight_reports", "ck_drop_insight_report_effects_status"
        ):
            op.create_check_constraint(
                "ck_drop_insight_report_effects_status",
                "drop_insight_reports",
                "effects_status IN ('PENDING', 'APPLYING', 'APPLIED')",
            )
        if not _has_check(
            bind, "drop_insight_reports", "ck_drop_insight_report_effects_phase"
        ):
            op.create_check_constraint(
                "ck_drop_insight_report_effects_phase",
                "drop_insight_reports",
                "effects_phase IS NULL OR effects_phase IN "
                "('EXECUTION_STARTED', 'EFFECTS_COMPLETED')",
            )
        if not _has_index(
            bind, "drop_insight_reports", "ix_drop_insight_reports_effects_lease"
        ):
            op.create_index(
                "ix_drop_insight_reports_effects_lease",
                "drop_insight_reports",
                ["effects_status", "effects_lease_expires_at"],
            )


def downgrade() -> None:
    # Adoption columns preserve historical audit data and are intentionally retained.
    pass
