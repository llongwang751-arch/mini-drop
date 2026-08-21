"""Bind diagnosis evidence snapshots to producing task attempts."""

from alembic import op
import sqlalchemy as sa

revision = "20260821_0013"
down_revision = "20260813_0012"
branch_labels = None
depends_on = None

_TABLE = "diagnosis_evidence_snapshots"
_INDEX = "ix_diagnosis_evidence_snapshots_attempt_id"
_FK = "fk_diagnosis_evidence_snapshots_attempt_id"


def _attempt_foreign_key(inspector: sa.Inspector) -> dict | None:
    for foreign_key in inspector.get_foreign_keys(_TABLE):
        if (
            foreign_key.get("referred_table") == "task_attempts"
            and foreign_key.get("constrained_columns") == ["attempt_id"]
        ):
            return foreign_key
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if _TABLE not in tables:
        return

    columns = {row["name"] for row in inspector.get_columns(_TABLE)}
    if "attempt_id" not in columns:
        op.add_column(_TABLE, sa.Column("attempt_id", sa.String(128), nullable=True))

    invalid = bind.execute(sa.text(f"""
        SELECT snapshot.id
        FROM {_TABLE} AS snapshot
        LEFT JOIN task_attempts AS attempt ON attempt.id = snapshot.attempt_id
        WHERE snapshot.attempt_id IS NOT NULL
          AND (attempt.id IS NULL OR snapshot.task_id IS NULL
               OR attempt.task_id <> snapshot.task_id)
        LIMIT 1
    """)).scalar()
    if invalid is not None:
        raise RuntimeError(
            "invalid diagnosis evidence snapshot attempt lineage: " + str(invalid)
        )

    inspector = sa.inspect(bind)
    indexes = {row["name"] for row in inspector.get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(_INDEX, _TABLE, ["attempt_id"])

    inspector = sa.inspect(bind)
    if _attempt_foreign_key(inspector) is None:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.create_foreign_key(
                _FK,
                "task_attempts",
                ["attempt_id"],
                ["id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return

    # Drop the explicit index before SQLite batch-rebuilds the table.  The
    # batch operation reflects the current table metadata and can otherwise
    # recreate an index that was still present when the copy started.
    indexes = {row["name"] for row in inspector.get_indexes(_TABLE)}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)

    foreign_key = _attempt_foreign_key(inspector)
    if foreign_key is not None:
        with op.batch_alter_table(
            _TABLE,
            naming_convention={"fk": _FK},
        ) as batch_op:
            batch_op.drop_constraint(foreign_key.get("name") or _FK, type_="foreignkey")

    # Batch mode may recreate indexes from its snapshot of the table.  Use a
    # direct DDL guard after the rebuild; Inspector metadata can be stale on
    # SQLite within the same Alembic connection.
    bind.execute(sa.text(f'DROP INDEX IF EXISTS "{_INDEX}"'))

    columns = {row["name"] for row in sa.inspect(bind).get_columns(_TABLE)}
    if "attempt_id" not in columns:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column("attempt_id")
    else:
        op.drop_column(_TABLE, "attempt_id")
