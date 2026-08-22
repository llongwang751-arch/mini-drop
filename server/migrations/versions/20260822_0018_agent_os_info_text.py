"""Allow structured Agent operating-system compatibility reports.

TLinux 2/3/4 capability discovery records distribution, kernel and collector
availability in ``agents.os_info``.  The former VARCHAR(256) column rejected a
valid registration before the first heartbeat could be persisted.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260822_0018"
down_revision = "20260821_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agents" not in set(inspector.get_table_names()):
        raise RuntimeError("cannot widen Agent OS information: agents is missing")

    columns = {item["name"]: item for item in inspector.get_columns("agents")}
    if "os_info" not in columns:
        raise RuntimeError("cannot widen Agent OS information: agents.os_info is missing")

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("agents") as batch:
            batch.alter_column(
                "os_info",
                existing_type=sa.String(length=256),
                type_=sa.Text(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "agents",
            "os_info",
            existing_type=sa.String(length=256),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # A compatibility report may already be longer than 256 bytes.  Shrinking
    # would either lose evidence or fail the rollback, so the safe downgrade
    # deliberately retains the wider storage type.
    pass
