"""Allow one diagnosis to compose multiple diagnostic skills."""

from alembic import op
import sqlalchemy as sa


revision = "20260827_0034"
down_revision = "20260825_0033"
branch_labels = None
depends_on = None

_TABLE = "diagnostic_skill_activations"
_OLD = "uq_diagnostic_skill_activation_diagnosis"
_NEW = "uq_diagnostic_skill_activation_diagnosis_skill"


def _unique_names(bind) -> set[str]:
    tables = set(sa.inspect(bind).get_table_names())
    if _TABLE not in tables:
        return set()
    return {
        item["name"]
        for item in sa.inspect(bind).get_unique_constraints(_TABLE)
        if item.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    names = _unique_names(bind)
    if _OLD in names:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_OLD, type_="unique")
    if _NEW not in _unique_names(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_unique_constraint(_NEW, ["diagnosis_id", "skill_id"])


def downgrade() -> None:
    bind = op.get_bind()
    names = _unique_names(bind)
    if _NEW in names:
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_NEW, type_="unique")
    if _OLD not in _unique_names(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_unique_constraint(_OLD, ["diagnosis_id"])
