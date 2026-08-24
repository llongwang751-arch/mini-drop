"""Add durable authority digest for task attempts."""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0032"
down_revision = "20260824_0031"
branch_labels = None
depends_on = None

_TABLE = "task_attempts"
_COLUMN = "task_attempt_authority_sha256"
_INDEX = "uq_task_attempts_authority_sha256"
_OWNERSHIP_TABLE = "migration_20260824_0032_ownership"


def _column_names(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(_TABLE)}


def _index_names(bind) -> set[str | None]:
    inspector = sa.inspect(bind)
    names = {item["name"] for item in inspector.get_indexes(_TABLE)}
    names.update(item["name"] for item in inspector.get_unique_constraints(_TABLE))
    return names


def _has_unique_authority(bind) -> bool:
    inspector = sa.inspect(bind)
    identities = list(inspector.get_unique_constraints(_TABLE))
    identities.extend(
        item for item in inspector.get_indexes(_TABLE) if item.get("unique")
    )
    return any(
        list(item.get("column_names") or []) == [_COLUMN]
        for item in identities
    )


def _record_ownership(bind, names: set[str]) -> None:
    if not names:
        return
    if _OWNERSHIP_TABLE not in sa.inspect(bind).get_table_names():
        op.create_table(
            _OWNERSHIP_TABLE,
            sa.Column("object_name", sa.String(length=128), primary_key=True),
        )
    for name in sorted(names):
        bind.execute(
            sa.text(f"INSERT INTO {_OWNERSHIP_TABLE} (object_name) VALUES (:name)"),
            {"name": name},
        )


def _owned(bind) -> set[str]:
    if _OWNERSHIP_TABLE not in sa.inspect(bind).get_table_names():
        return set()
    return {
        str(row[0])
        for row in bind.execute(sa.text(f"SELECT object_name FROM {_OWNERSHIP_TABLE}"))
    }


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        raise RuntimeError("task attempt authority migration requires task_attempts")
    owned: set[str] = set()
    if _COLUMN not in _column_names(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=64), nullable=True))
        owned.add("column:" + _COLUMN)
    if not _has_unique_authority(bind):
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=True)
        owned.add("index:" + _INDEX)
    _record_ownership(bind, owned)


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    owned = _owned(bind)
    if "index:" + _INDEX in owned and _INDEX in _index_names(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    if "column:" + _COLUMN in owned and _COLUMN in _column_names(bind):
        op.drop_column(_TABLE, _COLUMN)
    if _OWNERSHIP_TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(_OWNERSHIP_TABLE)
