"""Add persisted Skill experiments and explicit operator preferences."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from server.app.models import (
    DiagnosticExperimentAssignmentModel,
    DiagnosticExperimentMetricModel,
    DiagnosticExperimentModel,
    DiagnosticExperimentObservationModel,
    OperatorPreferenceMemoryModel,
)


revision = "20260908_0007"
down_revision = "20260901_0006"
branch_labels = None
depends_on = None


_NEW_TABLES = (
    DiagnosticExperimentModel.__table__,
    DiagnosticExperimentAssignmentModel.__table__,
    DiagnosticExperimentObservationModel.__table__,
    DiagnosticExperimentMetricModel.__table__,
    OperatorPreferenceMemoryModel.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "drop_insight_sessions" in tables:
        columns = {
            item["name"]
            for item in inspector.get_columns("drop_insight_sessions")
        }
        if "created_by" not in columns:
            op.add_column(
                "drop_insight_sessions",
                sa.Column(
                    "created_by",
                    sa.String(length=128),
                    nullable=False,
                    server_default="system:internal",
                ),
            )
            op.create_index(
                "ix_drop_insight_sessions_created_by",
                "drop_insight_sessions",
                ["created_by"],
            )
    for table in _NEW_TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_NEW_TABLES):
        table.drop(bind=bind, checkfirst=True)
    tables = set(inspect(bind).get_table_names())
    if "drop_insight_sessions" not in tables:
        return
    columns = {
        item["name"]
        for item in inspect(bind).get_columns("drop_insight_sessions")
    }
    if "created_by" in columns:
        indexes = {
            item.get("name")
            for item in inspect(bind).get_indexes("drop_insight_sessions")
        }
        if "ix_drop_insight_sessions_created_by" in indexes:
            op.drop_index(
                "ix_drop_insight_sessions_created_by",
                table_name="drop_insight_sessions",
            )
        op.drop_column("drop_insight_sessions", "created_by")
