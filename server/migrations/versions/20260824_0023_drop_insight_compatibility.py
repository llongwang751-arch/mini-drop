"""Add Drop Insight live scope and durable orchestration identities."""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0023"
down_revision = "20260823_0022"
branch_labels = None
depends_on = None

_REPORT_IDENTITY = "uq_drop_insight_report_identity"
_REPORT_IDENTITY_COLUMNS = ["diagnosis_id", "hypothesis_id"]


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(table_name)}


def _report_identity_kind(bind) -> str | None:
    for item in sa.inspect(bind).get_unique_constraints("drop_insight_reports"):
        if (
            item.get("name") == _REPORT_IDENTITY
            and item.get("column_names") == _REPORT_IDENTITY_COLUMNS
        ):
            return "constraint"
    for item in sa.inspect(bind).get_indexes("drop_insight_reports"):
        if (
            item.get("name") == _REPORT_IDENTITY
            and item.get("unique")
            and item.get("column_names") == _REPORT_IDENTITY_COLUMNS
        ):
            return "index"
    return None


def _has_equivalent_report_identity(bind) -> bool:
    identities = [
        item.get("column_names") or []
        for item in sa.inspect(bind).get_unique_constraints(
            "drop_insight_reports"
        )
    ]
    identities.extend(
        item.get("column_names") or []
        for item in sa.inspect(bind).get_indexes("drop_insight_reports")
        if item.get("unique")
    )
    return _REPORT_IDENTITY_COLUMNS in identities


def _require_tables(bind) -> None:
    required = {
        "drop_insight_sessions",
        "drop_insight_tool_calls",
        "drop_insight_reports",
    }
    missing = sorted(required - _tables(bind))
    if missing:
        raise RuntimeError(
            "Drop Insight compatibility migration requires tables: "
            + ", ".join(missing)
        )


def _reject_duplicate_reports(bind) -> None:
    duplicate = bind.execute(sa.text("""
        SELECT diagnosis_id, hypothesis_id, COUNT(*) AS copies
        FROM drop_insight_reports
        GROUP BY diagnosis_id, hypothesis_id
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "duplicate Drop Insight report identity prevents migration: "
            f"diagnosis_id={duplicate['diagnosis_id']}, "
            f"hypothesis_id={duplicate['hypothesis_id']}, "
            f"count={duplicate['copies']}"
        )


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> bool:
    if column.name in _columns(bind, table_name):
        return False
    op.add_column(table_name, column)
    return True


def upgrade() -> None:
    bind = op.get_bind()
    _require_tables(bind)
    _reject_duplicate_reports(bind)

    _add_column_if_missing(
        bind,
        "drop_insight_sessions",
        sa.Column("requested_time_range_json", sa.JSON(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "drop_insight_sessions",
        sa.Column("effective_time_range_json", sa.JSON(), nullable=True),
    )
    bind.execute(sa.text("""
        UPDATE drop_insight_sessions
        SET requested_time_range_json = time_range_json
        WHERE (
            requested_time_range_json IS NULL
            OR TRIM(CAST(requested_time_range_json AS TEXT)) IN ('', '{}', 'null')
        )
          AND time_range_json IS NOT NULL
          AND TRIM(CAST(time_range_json AS TEXT)) NOT IN ('', '{}', 'null')
    """))

    terminal_status_added = _add_column_if_missing(
        bind,
        "drop_insight_tool_calls",
        sa.Column(
            "terminal_processing_status",
            sa.String(length=32),
            nullable=True,
            server_default="NONE",
        ),
    )
    _add_column_if_missing(
        bind,
        "drop_insight_tool_calls",
        sa.Column("terminal_processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(sa.text("""
        UPDATE drop_insight_tool_calls
        SET terminal_processing_status = 'NONE'
        WHERE terminal_processing_status IS NULL
    """))
    status_column = next(
        item
        for item in sa.inspect(bind).get_columns("drop_insight_tool_calls")
        if item["name"] == "terminal_processing_status"
    )
    if terminal_status_added or status_column.get("nullable", True):
        with op.batch_alter_table("drop_insight_tool_calls") as batch:
            batch.alter_column(
                "terminal_processing_status",
                existing_type=sa.String(length=32),
                nullable=False,
                server_default="NONE",
            )

    if not _has_equivalent_report_identity(bind):
        with op.batch_alter_table("drop_insight_reports") as batch:
            batch.create_unique_constraint(
                _REPORT_IDENTITY,
                _REPORT_IDENTITY_COLUMNS,
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "drop_insight_reports" in tables:
        identity_kind = _report_identity_kind(bind)
        if identity_kind == "constraint":
            with op.batch_alter_table("drop_insight_reports") as batch:
                batch.drop_constraint(_REPORT_IDENTITY, type_="unique")
        elif identity_kind == "index":
            op.drop_index(_REPORT_IDENTITY, table_name="drop_insight_reports")

    if "drop_insight_tool_calls" in tables:
        columns = _columns(bind, "drop_insight_tool_calls")
        owned = [
            name
            for name in ("terminal_processed_at", "terminal_processing_status")
            if name in columns
        ]
        if owned:
            with op.batch_alter_table("drop_insight_tool_calls") as batch:
                for name in owned:
                    batch.drop_column(name)

    if "drop_insight_sessions" in tables:
        columns = _columns(bind, "drop_insight_sessions")
        owned = [
            name
            for name in ("effective_time_range_json", "requested_time_range_json")
            if name in columns
        ]
        if owned:
            with op.batch_alter_table("drop_insight_sessions") as batch:
                for name in owned:
                    batch.drop_column(name)
