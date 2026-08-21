"""Persist the public benchmark case identity on diagnosis sessions."""

from alembic import op
import sqlalchemy as sa

revision = "20260821_0017"
down_revision = "20260821_0016"
branch_labels = None
depends_on = None

_TABLE = "diagnosis_sessions"
_COLUMN = "case_id"
_INDEX = "ix_diagnosis_sessions_case_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        raise RuntimeError("cannot add diagnosis case identity: diagnosis_sessions is missing")

    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    if _COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(128), nullable=True))

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return

    indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name=_TABLE)

    columns = {item["name"] for item in sa.inspect(bind).get_columns(_TABLE)}
    if _COLUMN not in columns:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
    else:
        op.drop_column(_TABLE, _COLUMN)
