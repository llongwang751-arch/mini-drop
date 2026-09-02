"""Current Mini-Drop baseline: Go API, C++ control/agent, Python workers."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from server.app.models import Base


revision = "20260901_0001"
down_revision = "20260813_0012"
branch_labels = None
depends_on = None


def _has_constraint(bind: Connection, table_name: str, constraint_name: str) -> bool:
    inspector = inspect(bind)
    constraints = inspector.get_unique_constraints(table_name)
    constraints.extend(inspector.get_foreign_keys(table_name))
    return any(item.get("name") == constraint_name for item in constraints)


def _has_index(bind: Connection, table_name: str, index_name: str) -> bool:
    return any(
        item.get("name") == index_name
        for item in inspect(bind).get_indexes(table_name)
    )


def _adopt_legacy_postgresql(bind: Connection) -> None:
    """Add lineage columns and constraints without discarding legacy artifacts."""
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    required = {"task_attempts", "analysis_jobs", "artifacts"}
    if not required.issubset(tables):
        return

    artifact_columns = {
        column["name"] for column in inspector.get_columns("artifacts")
    }
    if "task_attempt_id" not in artifact_columns:
        op.add_column(
            "artifacts",
            sa.Column("task_attempt_id", sa.String(length=128), nullable=True),
        )
    if "analysis_job_id" not in artifact_columns:
        op.add_column(
            "artifacts",
            sa.Column("analysis_job_id", sa.String(length=128), nullable=True),
        )

    # A legacy task is adopted only when its concrete attempt is unambiguous.
    bind.execute(text("""
        UPDATE analysis_jobs AS job
        SET task_attempt_id = candidate.attempt_id
        FROM (
            SELECT task_id, min(id) AS attempt_id
            FROM task_attempts
            GROUP BY task_id
            HAVING count(*) = 1
        ) AS candidate
        WHERE job.task_id = candidate.task_id
          AND job.task_attempt_id IS NULL
    """))
    bind.execute(text("""
        UPDATE artifacts AS artifact
        SET task_attempt_id = candidate.attempt_id
        FROM (
            SELECT task_id, min(id) AS attempt_id
            FROM task_attempts
            GROUP BY task_id
            HAVING count(*) = 1
        ) AS candidate
        WHERE artifact.task_id = candidate.task_id
          AND artifact.task_attempt_id IS NULL
    """))

    if not _has_constraint(bind, "task_attempts", "uq_task_attempt_identity"):
        op.create_unique_constraint(
            "uq_task_attempt_identity", "task_attempts", ["id", "task_id"]
        )
    if not _has_constraint(bind, "analysis_jobs", "uq_analysis_job_attempt_identity"):
        op.create_unique_constraint(
            "uq_analysis_job_attempt_identity",
            "analysis_jobs",
            ["id", "task_id", "task_attempt_id"],
        )
    if not _has_constraint(bind, "artifacts", "uq_artifact_attempt_identity"):
        op.create_unique_constraint(
            "uq_artifact_attempt_identity",
            "artifacts",
            ["id", "task_id", "task_attempt_id"],
        )

    if not _has_constraint(bind, "analysis_jobs", "fk_analysis_jobs_task_attempt_identity"):
        op.create_foreign_key(
            "fk_analysis_jobs_task_attempt_identity",
            "analysis_jobs",
            "task_attempts",
            ["task_attempt_id", "task_id"],
            ["id", "task_id"],
        )
    if not _has_constraint(bind, "artifacts", "fk_artifacts_task_attempt_identity"):
        op.create_foreign_key(
            "fk_artifacts_task_attempt_identity",
            "artifacts",
            "task_attempts",
            ["task_attempt_id", "task_id"],
            ["id", "task_id"],
        )

    bind.execute(text("""
        UPDATE artifacts AS artifact
        SET analysis_job_id = job.id
        FROM analysis_jobs AS job
        CROSS JOIN LATERAL json_array_elements_text(
            COALESCE(job.output_artifact_ids_json, '[]'::json)
        ) AS output_ids(value)
        WHERE artifact.id = output_ids.value::integer
          AND artifact.task_id = job.task_id
          AND artifact.task_attempt_id = job.task_attempt_id
          AND job.task_attempt_id IS NOT NULL
          AND artifact.analysis_job_id IS NULL
    """))

    if not _has_constraint(bind, "artifacts", "fk_artifacts_analysis_job_identity"):
        op.create_foreign_key(
            "fk_artifacts_analysis_job_identity",
            "artifacts",
            "analysis_jobs",
            ["analysis_job_id", "task_id", "task_attempt_id"],
            ["id", "task_id", "task_attempt_id"],
        )
    if not _has_index(bind, "artifacts", "ix_artifacts_task_attempt_id"):
        op.create_index(
            "ix_artifacts_task_attempt_id", "artifacts", ["task_attempt_id"]
        )
    if not _has_index(bind, "artifacts", "ix_artifacts_analysis_job_id"):
        op.create_index(
            "ix_artifacts_analysis_job_id", "artifacts", ["analysis_job_id"]
        )


def _rebuild_legacy_analysis_links(bind: Connection) -> None:
    bind.execute(text("""
        INSERT INTO analysis_job_input_artifacts (
            analysis_job_id, artifact_id, task_id, task_attempt_id, created_at
        )
        SELECT job.id, artifact.id, job.task_id, job.task_attempt_id, job.created_at
        FROM analysis_jobs AS job
        CROSS JOIN LATERAL json_array_elements_text(
            COALESCE(job.input_artifact_ids_json, '[]'::json)
        ) AS input_ids(value)
        JOIN artifacts AS artifact ON artifact.id = input_ids.value::integer
        WHERE job.task_attempt_id IS NOT NULL
          AND artifact.task_id = job.task_id
          AND artifact.task_attempt_id = job.task_attempt_id
          AND artifact.analysis_job_id IS NULL
        ON CONFLICT (analysis_job_id, artifact_id) DO NOTHING
    """))
    bind.execute(text("""
        INSERT INTO analysis_job_output_artifacts (
            analysis_job_id, artifact_id, task_id, task_attempt_id, created_at
        )
        SELECT job.id, artifact.id, job.task_id, job.task_attempt_id, artifact.created_at
        FROM analysis_jobs AS job
        JOIN artifacts AS artifact ON artifact.analysis_job_id = job.id
        WHERE job.task_attempt_id IS NOT NULL
          AND artifact.task_id = job.task_id
          AND artifact.task_attempt_id = job.task_attempt_id
        ON CONFLICT (analysis_job_id, artifact_id) DO NOTHING
    """))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _adopt_legacy_postgresql(bind)
    Base.metadata.create_all(bind=bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        _rebuild_legacy_analysis_links(bind)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
