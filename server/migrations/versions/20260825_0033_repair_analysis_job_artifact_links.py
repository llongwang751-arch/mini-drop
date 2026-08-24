"""Repair analysis job artifact link tables on drifted deployments.

Revision 0032 introduced the authoritative TaskAttempt lineage.  A deployed
database can nevertheless be stamped at 0032 while the two association tables
are absent (for example, when an older migration image stamped the revision).
This forward-only repair makes that drift explicit and recoverable.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_0033"
down_revision = "20260824_0032"
branch_labels = None
depends_on = None

_OWNERSHIP_TABLE = "migration_20260825_0033_ownership"
_ASSOCIATIONS = {
    "analysis_job_input_artifacts": {
        "job_fk": "fk_analysis_job_inputs_job_identity",
        "artifact_fk": "fk_analysis_job_inputs_artifact_identity",
        "identity": "uq_analysis_job_input_artifact",
        "job_index": "ix_analysis_job_input_artifacts_analysis_job_id",
        "artifact_index": "ix_analysis_job_input_artifacts_artifact_id",
    },
    "analysis_job_output_artifacts": {
        "job_fk": "fk_analysis_job_outputs_job_identity",
        "artifact_fk": "fk_analysis_job_outputs_artifact_identity",
        "identity": "uq_analysis_job_output_artifact",
        "job_index": "ix_analysis_job_output_artifacts_analysis_job_id",
        "artifact_index": "ix_analysis_job_output_artifacts_artifact_id",
    },
}


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _create_association_table(table: str, names: dict[str, str]) -> None:
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("analysis_job_id", sa.String(length=128), nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("task_attempt_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_job_id", "task_id", "task_attempt_id"],
            ["analysis_jobs.id", "analysis_jobs.task_id", "analysis_jobs.task_attempt_id"],
            name=names["job_fk"],
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "task_id", "task_attempt_id"],
            ["artifacts.id", "artifacts.task_id", "artifacts.task_attempt_id"],
            name=names["artifact_fk"],
        ),
        sa.UniqueConstraint(
            "analysis_job_id", "artifact_id", name=names["identity"]
        ),
    )


def _record_ownership(bind, names: set[str]) -> None:
    if not names:
        return
    if _OWNERSHIP_TABLE not in _tables(bind):
        op.create_table(
            _OWNERSHIP_TABLE,
            sa.Column("object_name", sa.String(length=160), primary_key=True),
        )
    for name in sorted(names):
        bind.execute(
            sa.text(f"INSERT INTO {_OWNERSHIP_TABLE} (object_name) VALUES (:name)"),
            {"name": name},
        )


def _validate_existing_table(bind, table: str) -> None:
    columns = {
        item["name"]: item for item in sa.inspect(bind).get_columns(table)
    }
    required = {
        "id", "analysis_job_id", "artifact_id", "task_id",
        "task_attempt_id", "created_at",
    }
    missing = required - set(columns)
    if missing:
        raise RuntimeError(
            f"incompatible preexisting table {table}; missing columns: "
            + ", ".join(sorted(missing))
        )


def upgrade() -> None:
    bind = op.get_bind()
    missing_parents = {"analysis_jobs", "artifacts"} - _tables(bind)
    if missing_parents:
        raise RuntimeError(
            "analysis artifact repair requires tables: "
            + ", ".join(sorted(missing_parents))
        )

    owned: set[str] = set()
    for table, names in _ASSOCIATIONS.items():
        if table not in _tables(bind):
            _create_association_table(table, names)
            owned.add("table:" + table)
        else:
            _validate_existing_table(bind, table)

        indexes = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in sa.inspect(bind).get_indexes(table)
        }
        for index_name, column in (
            (names["job_index"], "analysis_job_id"),
            (names["artifact_index"], "artifact_id"),
        ):
            actual = indexes.get(index_name)
            if actual is not None and actual != (column,):
                raise RuntimeError(f"incompatible preexisting index {index_name}")
            if actual is None:
                op.create_index(index_name, table, [column])
                if "table:" + table not in owned:
                    owned.add("index:" + table + ":" + index_name)

    _record_ownership(bind, owned)


def downgrade() -> None:
    bind = op.get_bind()
    if _OWNERSHIP_TABLE not in _tables(bind):
        return
    owned = {
        str(row[0])
        for row in bind.execute(
            sa.text(f"SELECT object_name FROM {_OWNERSHIP_TABLE}")
        )
    }
    for table, names in reversed(tuple(_ASSOCIATIONS.items())):
        if table not in _tables(bind):
            continue
        if "table:" + table in owned:
            op.drop_table(table)
            continue
        existing_indexes = {
            item["name"] for item in sa.inspect(bind).get_indexes(table)
        }
        for index_name in (names["artifact_index"], names["job_index"]):
            if (
                "index:" + table + ":" + index_name in owned
                and index_name in existing_indexes
            ):
                op.drop_index(index_name, table_name=table)
    op.drop_table(_OWNERSHIP_TABLE)
