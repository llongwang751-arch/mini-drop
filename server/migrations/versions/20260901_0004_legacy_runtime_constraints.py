"""Add runtime identities and query indexes missing from legacy databases."""

from alembic import op
from sqlalchemy import inspect
from sqlalchemy.engine import Connection


revision = "20260901_0004"
down_revision = "20260901_0003"
branch_labels = None
depends_on = None


def _has_unique(bind: Connection, table_name: str, columns: list[str]) -> bool:
    return any(
        item.get("column_names") == columns
        for item in inspect(bind).get_unique_constraints(table_name)
    )


def _has_index(bind: Connection, table_name: str, index_name: str) -> bool:
    return any(
        item.get("name") == index_name
        for item in inspect(bind).get_indexes(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "drop_insight_reports" in tables and not _has_unique(
        bind, "drop_insight_reports", ["diagnosis_id", "hypothesis_id"]
    ):
        op.create_unique_constraint(
            "uq_drop_insight_report_identity",
            "drop_insight_reports",
            ["diagnosis_id", "hypothesis_id"],
        )

    indexes = (
        (
            "drop_insight_feedback",
            "ix_drop_insight_feedback_hypothesis_id",
            ["hypothesis_id"],
        ),
        (
            "drop_insight_feedback",
            "ix_drop_insight_feedback_report_id",
            ["report_id"],
        ),
        (
            "drop_insight_hypotheses",
            "ix_drop_insight_hypotheses_parent_hypothesis_id",
            ["parent_hypothesis_id"],
        ),
        (
            "fix_verifications",
            "ix_fix_verifications_diagnosis_id",
            ["diagnosis_id"],
        ),
        (
            "schedule_records",
            "ix_schedule_records_schedule_id",
            ["schedule_id"],
        ),
    )
    for table_name, index_name, columns in indexes:
        if table_name in tables and not _has_index(bind, table_name, index_name):
            op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    pass
