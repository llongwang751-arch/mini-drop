"""Add exact task-attempt authority and analysis lineage."""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "20260824_0032"
down_revision = "20260824_0031"
branch_labels = None
depends_on = None

_OWNERSHIP_TABLE = "migration_20260824_0032_ownership"
_AUTHORITY_COLUMN = "task_attempt_authority_sha256"
_AUTHORITY_INDEX = "uq_task_attempts_authority_sha256"

_TASK_ATTEMPT_IDENTITY = "uq_task_attempt_identity"
_ANALYSIS_JOB_IDENTITY = "uq_analysis_job_attempt_identity"
_ARTIFACT_IDENTITY = "uq_artifact_attempt_identity"
_ANALYSIS_JOB_ATTEMPT_FK = "fk_analysis_jobs_task_attempt_identity"
_ARTIFACT_ATTEMPT_FK = "fk_artifacts_task_attempt_identity"
_ARTIFACT_ANALYSIS_JOB_FK = "fk_artifacts_analysis_job_identity"

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

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> dict[str, dict]:
    return {
        str(item["name"]): item
        for item in sa.inspect(bind).get_columns(table)
    }


def _indexes(bind, table: str) -> dict[str, dict]:
    return {
        str(item["name"]): item
        for item in sa.inspect(bind).get_indexes(table)
        if item.get("name")
    }


def _identities(bind, table: str) -> dict[str, tuple[str, ...]]:
    identities: dict[str, tuple[str, ...]] = {}
    for item in sa.inspect(bind).get_unique_constraints(table):
        if item.get("name"):
            identities[str(item["name"])] = tuple(item.get("column_names") or ())
    for item in sa.inspect(bind).get_indexes(table):
        if item.get("unique") and item.get("name"):
            identities[str(item["name"])] = tuple(item.get("column_names") or ())
    return identities


def _foreign_keys(
    bind, table: str
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    foreign_keys = {}
    for item in sa.inspect(bind).get_foreign_keys(table):
        name = item.get("name")
        if name:
            foreign_keys[str(name)] = (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table") or ""),
                tuple(item.get("referred_columns") or ()),
            )
    return foreign_keys


def _has_identity(bind, table: str, columns: tuple[str, ...]) -> bool:
    return columns in _identities(bind, table).values()


def _has_foreign_key(
    bind,
    table: str,
    signature: tuple[tuple[str, ...], str, tuple[str, ...]],
) -> bool:
    return signature in _foreign_keys(bind, table).values()


def _require_named_signature(
    objects: dict[str, tuple],
    name: str,
    signature: tuple,
    kind: str,
) -> None:
    actual = objects.get(name)
    if actual is not None and actual != signature:
        raise RuntimeError(
            f"incompatible preexisting {kind} {name}: "
            f"expected {signature}, found {actual}"
        )


def _require_string_column(
    bind,
    table: str,
    column: str,
    *,
    length: int,
    nullable: bool,
) -> None:
    item = _columns(bind, table).get(column)
    if item is None:
        return
    column_type = item.get("type")
    actual_length = getattr(column_type, "length", None)
    if (
        not isinstance(column_type, sa.String)
        or actual_length != length
        or bool(item.get("nullable")) != nullable
    ):
        raise RuntimeError(
            f"incompatible preexisting column {table}.{column}; "
            f"expected VARCHAR({length}) nullable={nullable}"
        )


def _require_integer_column(
    bind,
    table: str,
    column: str,
    *,
    nullable: bool,
) -> None:
    item = _columns(bind, table).get(column)
    if item is None:
        return
    if (
        not isinstance(item.get("type"), sa.Integer)
        or bool(item.get("nullable")) != nullable
    ):
        raise RuntimeError(
            f"incompatible preexisting column {table}.{column}; "
            f"expected INTEGER nullable={nullable}"
        )


def _record_ownership(bind, names: set[str]) -> None:
    if not names:
        return
    if _OWNERSHIP_TABLE not in _tables(bind):
        op.create_table(
            _OWNERSHIP_TABLE,
            sa.Column("object_name", sa.String(length=160), primary_key=True),
        )
    existing = {
        str(row[0])
        for row in bind.execute(
            sa.text(f"SELECT object_name FROM {_OWNERSHIP_TABLE}")
        )
    }
    for name in sorted(names - existing):
        bind.execute(
            sa.text(
                f"INSERT INTO {_OWNERSHIP_TABLE} (object_name) VALUES (:name)"
            ),
            {"name": name},
        )


def _owned(bind) -> set[str]:
    if _OWNERSHIP_TABLE not in _tables(bind):
        return set()
    return {
        str(row[0])
        for row in bind.execute(
            sa.text(f"SELECT object_name FROM {_OWNERSHIP_TABLE}")
        )
    }


def _preflight_authorities(bind) -> None:
    if _AUTHORITY_COLUMN not in _columns(bind, "task_attempts"):
        return
    duplicate = bind.execute(sa.text(f"""
        SELECT {_AUTHORITY_COLUMN}
        FROM task_attempts
        WHERE {_AUTHORITY_COLUMN} IS NOT NULL
        GROUP BY {_AUTHORITY_COLUMN}
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).scalar()
    if duplicate is not None:
        raise RuntimeError(
            "duplicate task attempt authority digest prevents schema upgrade"
        )
    invalid = bind.execute(sa.text(f"""
        SELECT id, {_AUTHORITY_COLUMN}
        FROM task_attempts
        WHERE {_AUTHORITY_COLUMN} IS NOT NULL
    """)).mappings()
    for row in invalid:
        value = row[_AUTHORITY_COLUMN]
        if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
            raise RuntimeError(
                "invalid task attempt authority digest prevents schema upgrade: "
                + str(row["id"])
            )


def _preflight_lineage(bind) -> None:
    if "task_attempt_id" in _columns(bind, "analysis_jobs"):
        invalid = bind.execute(sa.text("""
            SELECT job.id
            FROM analysis_jobs AS job
            LEFT JOIN task_attempts AS attempt
              ON attempt.id = job.task_attempt_id
             AND attempt.task_id = job.task_id
            WHERE job.task_attempt_id IS NOT NULL
              AND attempt.id IS NULL
            LIMIT 1
        """)).scalar()
        if invalid is not None:
            raise RuntimeError(
                "invalid analysis job task attempt lineage: " + str(invalid)
            )

    artifact_columns = _columns(bind, "artifacts")
    if "task_attempt_id" in artifact_columns:
        invalid = bind.execute(sa.text("""
            SELECT artifact.id
            FROM artifacts AS artifact
            LEFT JOIN task_attempts AS attempt
              ON attempt.id = artifact.task_attempt_id
             AND attempt.task_id = artifact.task_id
            WHERE artifact.task_attempt_id IS NOT NULL
              AND attempt.id IS NULL
            LIMIT 1
        """)).scalar()
        if invalid is not None:
            raise RuntimeError(
                "invalid artifact task attempt lineage: " + str(invalid)
            )
    if {"task_attempt_id", "analysis_job_id"} <= set(artifact_columns):
        invalid = bind.execute(sa.text("""
            SELECT artifact.id
            FROM artifacts AS artifact
            LEFT JOIN analysis_jobs AS job
              ON job.id = artifact.analysis_job_id
             AND job.task_id = artifact.task_id
             AND job.task_attempt_id = artifact.task_attempt_id
            WHERE artifact.analysis_job_id IS NOT NULL
              AND job.id IS NULL
            LIMIT 1
        """)).scalar()
        if invalid is not None:
            raise RuntimeError(
                "invalid artifact analysis job lineage: " + str(invalid)
            )

    for table in _ASSOCIATIONS:
        if table not in _tables(bind):
            continue
        invalid = bind.execute(sa.text(f"""
            SELECT binding.id
            FROM {table} AS binding
            LEFT JOIN analysis_jobs AS job
              ON job.id = binding.analysis_job_id
             AND job.task_id = binding.task_id
             AND job.task_attempt_id = binding.task_attempt_id
            LEFT JOIN artifacts AS artifact
              ON artifact.id = binding.artifact_id
             AND artifact.task_id = binding.task_id
             AND artifact.task_attempt_id = binding.task_attempt_id
            WHERE job.id IS NULL OR artifact.id IS NULL
            LIMIT 1
        """)).scalar()
        if invalid is not None:
            raise RuntimeError(
                f"invalid {table} lineage: " + str(invalid)
            )


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


def _ensure_association(bind, table: str, names: dict[str, str], owned: set[str]) -> None:
    if table not in _tables(bind):
        _create_association_table(table, names)
        owned.add("table:" + table)
    else:
        _require_integer_column(bind, table, "id", nullable=False)
        _require_string_column(
            bind, table, "analysis_job_id", length=128, nullable=False
        )
        _require_integer_column(bind, table, "artifact_id", nullable=False)
        _require_string_column(bind, table, "task_id", length=128, nullable=False)
        _require_string_column(
            bind, table, "task_attempt_id", length=128, nullable=False
        )
        required = {
            "id", "analysis_job_id", "artifact_id", "task_id",
            "task_attempt_id", "created_at",
        }
        missing = required - set(_columns(bind, table))
        if missing:
            raise RuntimeError(
                f"incompatible preexisting table {table}; missing columns: "
                + ", ".join(sorted(missing))
            )

        identity = ("analysis_job_id", "artifact_id")
        identities = _identities(bind, table)
        _require_named_signature(
            identities, names["identity"], identity, "unique identity"
        )
        if identity not in identities.values():
            with op.batch_alter_table(table) as batch:
                batch.create_unique_constraint(names["identity"], list(identity))
            owned.add("unique:" + table + ":" + names["identity"])

        expected_foreign_keys = {
            names["job_fk"]: (
                ("analysis_job_id", "task_id", "task_attempt_id"),
                "analysis_jobs",
                ("id", "task_id", "task_attempt_id"),
            ),
            names["artifact_fk"]: (
                ("artifact_id", "task_id", "task_attempt_id"),
                "artifacts",
                ("id", "task_id", "task_attempt_id"),
            ),
        }
        for name, signature in expected_foreign_keys.items():
            foreign_keys = _foreign_keys(bind, table)
            _require_named_signature(
                foreign_keys, name, signature, "foreign key"
            )
            if signature not in foreign_keys.values():
                with op.batch_alter_table(table) as batch:
                    batch.create_foreign_key(
                        name,
                        signature[1],
                        list(signature[0]),
                        list(signature[2]),
                    )
                owned.add("foreign-key:" + table + ":" + name)

    for index_name, column in (
        (names["job_index"], "analysis_job_id"),
        (names["artifact_index"], "artifact_id"),
    ):
        indexes = _indexes(bind, table)
        actual = indexes.get(index_name)
        if actual is not None and tuple(actual.get("column_names") or ()) != (column,):
            raise RuntimeError(
                f"incompatible preexisting index {index_name}"
            )
        if actual is None:
            op.create_index(index_name, table, [column])
            if "table:" + table not in owned:
                owned.add("index:" + table + ":" + index_name)


def upgrade() -> None:
    bind = op.get_bind()
    required_tables = {"tasks", "task_attempts", "analysis_jobs", "artifacts"}
    missing_tables = required_tables - _tables(bind)
    if missing_tables:
        raise RuntimeError(
            "task attempt lineage migration requires tables: "
            + ", ".join(sorted(missing_tables))
        )

    _require_string_column(
        bind, "task_attempts", _AUTHORITY_COLUMN, length=64, nullable=True
    )
    _require_string_column(
        bind, "analysis_jobs", "task_attempt_id", length=128, nullable=True
    )
    _require_string_column(
        bind, "artifacts", "task_attempt_id", length=128, nullable=True
    )
    _require_string_column(
        bind, "artifacts", "analysis_job_id", length=128, nullable=True
    )

    owned: set[str] = set()
    if _AUTHORITY_COLUMN not in _columns(bind, "task_attempts"):
        op.add_column(
            "task_attempts",
            sa.Column(_AUTHORITY_COLUMN, sa.String(length=64), nullable=True),
        )
        owned.add("column:task_attempts:" + _AUTHORITY_COLUMN)
    if "task_attempt_id" not in _columns(bind, "analysis_jobs"):
        op.add_column(
            "analysis_jobs",
            sa.Column("task_attempt_id", sa.String(length=128), nullable=True),
        )
        owned.add("column:analysis_jobs:task_attempt_id")
    if "task_attempt_id" not in _columns(bind, "artifacts"):
        op.add_column(
            "artifacts",
            sa.Column("task_attempt_id", sa.String(length=128), nullable=True),
        )
        owned.add("column:artifacts:task_attempt_id")
    if "analysis_job_id" not in _columns(bind, "artifacts"):
        op.add_column(
            "artifacts",
            sa.Column("analysis_job_id", sa.String(length=128), nullable=True),
        )
        owned.add("column:artifacts:analysis_job_id")

    _preflight_authorities(bind)
    _preflight_lineage(bind)

    task_attempt_identity = ("id", "task_id")
    task_attempt_identities = _identities(bind, "task_attempts")
    _require_named_signature(
        task_attempt_identities,
        _TASK_ATTEMPT_IDENTITY,
        task_attempt_identity,
        "unique identity",
    )
    if task_attempt_identity not in task_attempt_identities.values():
        with op.batch_alter_table("task_attempts") as batch:
            batch.create_unique_constraint(
                _TASK_ATTEMPT_IDENTITY, list(task_attempt_identity)
            )
        owned.add("unique:task_attempts:" + _TASK_ATTEMPT_IDENTITY)

    analysis_job_identity = ("id", "task_id", "task_attempt_id")
    analysis_job_identities = _identities(bind, "analysis_jobs")
    _require_named_signature(
        analysis_job_identities,
        _ANALYSIS_JOB_IDENTITY,
        analysis_job_identity,
        "unique identity",
    )
    analysis_job_fk = (
        ("task_attempt_id", "task_id"),
        "task_attempts",
        ("id", "task_id"),
    )
    analysis_job_foreign_keys = _foreign_keys(bind, "analysis_jobs")
    _require_named_signature(
        analysis_job_foreign_keys,
        _ANALYSIS_JOB_ATTEMPT_FK,
        analysis_job_fk,
        "foreign key",
    )
    add_analysis_identity = analysis_job_identity not in analysis_job_identities.values()
    add_analysis_fk = analysis_job_fk not in analysis_job_foreign_keys.values()
    if add_analysis_identity or add_analysis_fk:
        with op.batch_alter_table("analysis_jobs") as batch:
            if add_analysis_identity:
                batch.create_unique_constraint(
                    _ANALYSIS_JOB_IDENTITY, list(analysis_job_identity)
                )
            if add_analysis_fk:
                batch.create_foreign_key(
                    _ANALYSIS_JOB_ATTEMPT_FK,
                    "task_attempts",
                    list(analysis_job_fk[0]),
                    list(analysis_job_fk[2]),
                )
        if add_analysis_identity:
            owned.add("unique:analysis_jobs:" + _ANALYSIS_JOB_IDENTITY)
        if add_analysis_fk:
            owned.add("foreign-key:analysis_jobs:" + _ANALYSIS_JOB_ATTEMPT_FK)

    artifact_identity = ("id", "task_id", "task_attempt_id")
    artifact_identities = _identities(bind, "artifacts")
    _require_named_signature(
        artifact_identities,
        _ARTIFACT_IDENTITY,
        artifact_identity,
        "unique identity",
    )
    artifact_foreign_keys = _foreign_keys(bind, "artifacts")
    artifact_attempt_fk = (
        ("task_attempt_id", "task_id"),
        "task_attempts",
        ("id", "task_id"),
    )
    artifact_analysis_fk = (
        ("analysis_job_id", "task_id", "task_attempt_id"),
        "analysis_jobs",
        ("id", "task_id", "task_attempt_id"),
    )
    _require_named_signature(
        artifact_foreign_keys,
        _ARTIFACT_ATTEMPT_FK,
        artifact_attempt_fk,
        "foreign key",
    )
    _require_named_signature(
        artifact_foreign_keys,
        _ARTIFACT_ANALYSIS_JOB_FK,
        artifact_analysis_fk,
        "foreign key",
    )
    add_artifact_identity = artifact_identity not in artifact_identities.values()
    add_artifact_attempt_fk = artifact_attempt_fk not in artifact_foreign_keys.values()
    add_artifact_analysis_fk = artifact_analysis_fk not in artifact_foreign_keys.values()
    if add_artifact_identity or add_artifact_attempt_fk or add_artifact_analysis_fk:
        with op.batch_alter_table("artifacts") as batch:
            if add_artifact_identity:
                batch.create_unique_constraint(
                    _ARTIFACT_IDENTITY, list(artifact_identity)
                )
            if add_artifact_attempt_fk:
                batch.create_foreign_key(
                    _ARTIFACT_ATTEMPT_FK,
                    "task_attempts",
                    list(artifact_attempt_fk[0]),
                    list(artifact_attempt_fk[2]),
                )
            if add_artifact_analysis_fk:
                batch.create_foreign_key(
                    _ARTIFACT_ANALYSIS_JOB_FK,
                    "analysis_jobs",
                    list(artifact_analysis_fk[0]),
                    list(artifact_analysis_fk[2]),
                )
        if add_artifact_identity:
            owned.add("unique:artifacts:" + _ARTIFACT_IDENTITY)
        if add_artifact_attempt_fk:
            owned.add("foreign-key:artifacts:" + _ARTIFACT_ATTEMPT_FK)
        if add_artifact_analysis_fk:
            owned.add("foreign-key:artifacts:" + _ARTIFACT_ANALYSIS_JOB_FK)

    for table, names in _ASSOCIATIONS.items():
        _ensure_association(bind, table, names, owned)

    required_indexes = {
        "task_attempts": {
            _AUTHORITY_INDEX: (_AUTHORITY_COLUMN, True),
        },
        "analysis_jobs": {
            "ix_analysis_jobs_task_attempt_id": ("task_attempt_id", False),
        },
        "artifacts": {
            "ix_artifacts_task_attempt_id": ("task_attempt_id", False),
            "ix_artifacts_analysis_job_id": ("analysis_job_id", False),
        },
    }
    for table, expected in required_indexes.items():
        for name, (column, unique) in expected.items():
            indexes = _indexes(bind, table)
            actual = indexes.get(name)
            if actual is not None and (
                tuple(actual.get("column_names") or ()) != (column,)
                or bool(actual.get("unique")) != unique
            ):
                raise RuntimeError(f"incompatible preexisting index {name}")
            if name == _AUTHORITY_INDEX and _has_identity(
                bind, table, (column,)
            ):
                continue
            if actual is None:
                op.create_index(name, table, [column], unique=unique)
                owned.add("index:" + table + ":" + name)

    _record_ownership(bind, owned)


def _drop_owned_constraints(bind, table: str, owned: set[str]) -> None:
    unique_prefix = "unique:" + table + ":"
    fk_prefix = "foreign-key:" + table + ":"
    unique_names = [
        item.removeprefix(unique_prefix)
        for item in owned
        if item.startswith(unique_prefix)
    ]
    foreign_key_names = [
        item.removeprefix(fk_prefix)
        for item in owned
        if item.startswith(fk_prefix)
    ]
    existing_identities = _identities(bind, table)
    existing_foreign_keys = _foreign_keys(bind, table)
    unique_names = [name for name in unique_names if name in existing_identities]
    foreign_key_names = [
        name for name in foreign_key_names if name in existing_foreign_keys
    ]
    if not unique_names and not foreign_key_names:
        return
    with op.batch_alter_table(table) as batch:
        for name in foreign_key_names:
            batch.drop_constraint(name, type_="foreignkey")
        for name in unique_names:
            batch.drop_constraint(name, type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    owned = _owned(bind)

    for table, names in reversed(tuple(_ASSOCIATIONS.items())):
        if table not in _tables(bind):
            continue
        if "table:" + table in owned:
            op.drop_table(table)
            continue
        for index_name in (names["job_index"], names["artifact_index"]):
            marker = "index:" + table + ":" + index_name
            if marker in owned and index_name in _indexes(bind, table):
                op.drop_index(index_name, table_name=table)
        _drop_owned_constraints(bind, table, owned)

    for table in ("artifacts", "analysis_jobs", "task_attempts"):
        if table not in _tables(bind):
            continue
        for marker in sorted(owned):
            prefix = "index:" + table + ":"
            if marker.startswith(prefix):
                name = marker.removeprefix(prefix)
                if name in _indexes(bind, table):
                    op.drop_index(name, table_name=table)
        _drop_owned_constraints(bind, table, owned)

        owned_columns = [
            marker.removeprefix("column:" + table + ":")
            for marker in owned
            if marker.startswith("column:" + table + ":")
            and marker.removeprefix("column:" + table + ":")
            in _columns(bind, table)
        ]
        if owned_columns:
            if bind.dialect.name == "sqlite":
                with op.batch_alter_table(table) as batch:
                    for column in owned_columns:
                        batch.drop_column(column)
            else:
                for column in owned_columns:
                    op.drop_column(table, column)

    if _OWNERSHIP_TABLE in _tables(bind):
        op.drop_table(_OWNERSHIP_TABLE)
