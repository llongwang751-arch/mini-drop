"""Add durable Agent Runtime turn recovery ownership."""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0026"
down_revision = "20260824_0025"
branch_labels = None
depends_on = None

_TABLE = "agent_runtime_turns"
_INDEX = "ix_agent_runtime_turns_recovery_lease"
_CONSTRAINT = "ck_agent_runtime_turn_recovery_phase"


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(_TABLE)}


def _indexes(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)}


def _add_column_if_missing(bind, column: sa.Column) -> None:
    if column.name not in _columns(bind):
        op.add_column(_TABLE, column)


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in _tables(bind):
        raise RuntimeError(
            "Agent Runtime recovery migration requires table: " + _TABLE
        )

    _add_column_if_missing(
        bind,
        sa.Column("recovery_phase", sa.String(length=32), nullable=True),
    )
    _add_column_if_missing(
        bind,
        sa.Column("recovery_owner", sa.String(length=128), nullable=True),
    )
    _add_column_if_missing(
        bind,
        sa.Column(
            "recovery_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    _add_column_if_missing(
        bind,
        sa.Column(
            "recovery_fencing_token",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    bind.execute(sa.text("""
        UPDATE agent_runtime_turns
        SET recovery_phase = CASE
                WHEN runtime_session_id IS NULL THEN 'NEEDS_BINDING'
                ELSE 'SUBMIT_INTENT'
            END,
            recovery_owner = NULL,
            recovery_lease_expires_at = NULL,
            recovery_fencing_token = COALESCE(recovery_fencing_token, 0)
        WHERE status IN ('SUBMITTING', 'ACCEPTANCE_UNKNOWN')
    """))
    bind.execute(sa.text("""
        UPDATE agent_runtime_turns
        SET recovery_phase = NULL,
            recovery_owner = NULL,
            recovery_lease_expires_at = NULL,
            recovery_fencing_token = COALESCE(recovery_fencing_token, 0)
        WHERE status NOT IN ('SUBMITTING', 'ACCEPTANCE_UNKNOWN')
    """))

    if _INDEX not in _indexes(bind):
        op.create_index(
            _INDEX,
            _TABLE,
            ["status", "recovery_lease_expires_at"],
            unique=False,
        )

    constraints = {
        item["name"]
        for item in sa.inspect(bind).get_check_constraints(_TABLE)
    }
    if _CONSTRAINT not in constraints:
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_check_constraint(
                _CONSTRAINT,
                "recovery_phase IS NULL OR recovery_phase IN "
                "('NEEDS_BINDING', 'SUBMIT_INTENT')",
            )


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in _tables(bind):
        return
    if _INDEX in _indexes(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    constraints = {
        item["name"]
        for item in sa.inspect(bind).get_check_constraints(_TABLE)
    }
    owned = [
        name
        for name in (
            "recovery_fencing_token",
            "recovery_lease_expires_at",
            "recovery_owner",
            "recovery_phase",
        )
        if name in _columns(bind)
    ]
    if owned or _CONSTRAINT in constraints:
        with op.batch_alter_table(_TABLE) as batch:
            if _CONSTRAINT in constraints:
                batch.drop_constraint(_CONSTRAINT, type_="check")
            for name in owned:
                batch.drop_column(name)
