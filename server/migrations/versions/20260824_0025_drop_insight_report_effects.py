"""Add durable Drop Insight report-effect state."""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0025"
down_revision = "20260824_0023"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(table_name)}


def _add_column_if_missing(bind, column: sa.Column) -> bool:
    if column.name in _columns(bind, "drop_insight_reports"):
        return False
    op.add_column("drop_insight_reports", column)
    return True


def _indexes(bind) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(bind).get_indexes("drop_insight_reports")
    }


def _check_constraints(bind) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(bind).get_check_constraints(
            "drop_insight_reports"
        )
    }


def upgrade() -> None:
    bind = op.get_bind()
    if "drop_insight_reports" not in _tables(bind):
        raise RuntimeError(
            "Drop Insight report effects migration requires table: "
            "drop_insight_reports"
        )

    status_added = _add_column_if_missing(
        bind,
        sa.Column(
            "effects_status",
            sa.String(length=32),
            nullable=True,
            server_default="PENDING",
        ),
    )
    _add_column_if_missing(
        bind,
        sa.Column(
            "effects_applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    bind.execute(sa.text("""
        UPDATE drop_insight_reports
        SET effects_status = 'PENDING', effects_applied_at = NULL
        WHERE effects_status IS NULL
           OR effects_status NOT IN ('PENDING', 'APPLIED')
    """))
    status_column = next(
        item
        for item in sa.inspect(bind).get_columns("drop_insight_reports")
        if item["name"] == "effects_status"
    )
    if status_added or status_column.get("nullable", True):
        with op.batch_alter_table("drop_insight_reports") as batch:
            batch.alter_column(
                "effects_status",
                existing_type=sa.String(length=32),
                nullable=False,
                server_default="PENDING",
            )


def downgrade() -> None:
    bind = op.get_bind()
    if "drop_insight_reports" not in _tables(bind):
        return
    columns = _columns(bind, "drop_insight_reports")
    owned = [
        name
        for name in ("effects_applied_at", "effects_status")
        if name in columns
    ]
    if not owned:
        return

    lease_index = "ix_drop_insight_reports_effects_lease"
    status_constraint = "ck_drop_insight_report_effects_status"
    indexes = _indexes(bind)
    constraints = _check_constraints(bind)
    if lease_index in indexes:
        op.drop_index(lease_index, table_name="drop_insight_reports")
    with op.batch_alter_table("drop_insight_reports") as batch:
        if status_constraint in constraints:
            batch.drop_constraint(status_constraint, type_="check")
        for name in owned:
            batch.drop_column(name)
