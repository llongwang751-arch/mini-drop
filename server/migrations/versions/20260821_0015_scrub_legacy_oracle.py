"""Remove the legacy diagnosis-side Oracle column.

The old column is scrubbed before removal.  No Oracle values are selected or
reported; the migration only uses the affected-row count internally.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260821_0015"
down_revision = "20260821_0014"
branch_labels = None
depends_on = None

_TABLE = "diagnosis_sessions"
_COLUMN = "evaluation_oracle_json"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    if _COLUMN not in columns:
        return

    # Scrub without selecting or logging the private values.  The assignment
    # is deliberately separate from the structural drop for auditability.
    bind.execute(sa.text(
        f'UPDATE "{_TABLE}" SET "{_COLUMN}" = NULL '
        f'WHERE "{_COLUMN}" IS NOT NULL'
    ))
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
    else:
        op.drop_column(_TABLE, _COLUMN)


def downgrade() -> None:
    # The legacy Oracle contract must not be recreated by downgrade.
    pass
