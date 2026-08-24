"""Add durable Drop Insight report-effect authority."""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0028"
down_revision = "20260824_0027"
branch_labels = None
depends_on = None

_TABLE = "drop_insight_reports"
_INDEX = "ix_drop_insight_reports_effects_lease"
_STATUS_CONSTRAINT = "ck_drop_insight_report_effects_status"
_PHASE_CONSTRAINT = "ck_drop_insight_report_effects_phase"
_OWNERSHIP_TABLE = "migration_20260824_0028_ownership"
_AUTHORITY_COLUMNS = (
    "effects_phase",
    "effects_owner",
    "effects_lease_expires_at",
    "effects_fencing_token",
)


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(_TABLE)}


def _indexes(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)}


def _constraints(bind) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(bind).get_check_constraints(_TABLE)
    }


def _add_column_if_missing(bind, column: sa.Column) -> bool:
    if column.name in _columns(bind):
        return False
    op.add_column(_TABLE, column)
    return True


def _record_ownership(
    bind,
    *,
    columns: set[str],
    index: bool,
    constraints: set[str],
) -> None:
    if _OWNERSHIP_TABLE not in _tables(bind):
        op.create_table(
            _OWNERSHIP_TABLE,
            sa.Column("object_name", sa.String(length=128), primary_key=True),
        )
    owned = set(columns)
    if index:
        owned.add(_INDEX)
    owned.update(constraints)
    for object_name in sorted(owned):
        bind.execute(
            sa.text(
                f"INSERT INTO {_OWNERSHIP_TABLE} (object_name) "
                "VALUES (:object_name)"
            ),
            {"object_name": object_name},
        )


def _owned_objects(bind) -> set[str]:
    if _OWNERSHIP_TABLE not in _tables(bind):
        return set()
    return {
        str(row[0])
        for row in bind.execute(
            sa.text(f"SELECT object_name FROM {_OWNERSHIP_TABLE}")
        )
    }


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in _tables(bind):
        raise RuntimeError(
            "Drop Insight report-effect migration requires table: " + _TABLE
        )

    added_columns = set()
    if _add_column_if_missing(
        bind,
        sa.Column("effects_phase", sa.String(length=32), nullable=True),
    ):
        added_columns.add("effects_phase")
    if _add_column_if_missing(
        bind,
        sa.Column("effects_owner", sa.String(length=128), nullable=True),
    ):
        added_columns.add("effects_owner")
    if _add_column_if_missing(
        bind,
        sa.Column(
            "effects_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    ):
        added_columns.add("effects_lease_expires_at")
    if _add_column_if_missing(
        bind,
        sa.Column(
            "effects_fencing_token",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    ):
        added_columns.add("effects_fencing_token")

    bind.execute(sa.text("""
        UPDATE drop_insight_reports
        SET effects_status = 'PENDING',
            effects_owner = NULL,
            effects_lease_expires_at = NULL,
            effects_fencing_token = COALESCE(effects_fencing_token, 0),
            effects_applied_at = NULL
        WHERE effects_status IS NULL
           OR effects_status NOT IN ('PENDING', 'APPLYING', 'APPLIED')
    """))
    bind.execute(sa.text("""
        UPDATE drop_insight_reports
        SET effects_owner = NULL,
            effects_lease_expires_at = NULL,
            effects_fencing_token = COALESCE(effects_fencing_token, 0),
            effects_applied_at = NULL
        WHERE effects_status = 'PENDING'
    """))
    bind.execute(sa.text("""
        UPDATE drop_insight_reports
        SET effects_status = 'PENDING',
            effects_owner = NULL,
            effects_lease_expires_at = NULL,
            effects_fencing_token = COALESCE(effects_fencing_token, 0),
            effects_applied_at = NULL
        WHERE effects_status = 'APPLYING'
          AND (effects_owner IS NULL OR effects_lease_expires_at IS NULL)
    """))
    bind.execute(sa.text("""
        UPDATE drop_insight_reports
        SET effects_applied_at = NULL
        WHERE effects_status = 'APPLYING'
          AND effects_owner IS NOT NULL
          AND effects_lease_expires_at IS NOT NULL
    """))
    bind.execute(sa.text("""
        UPDATE drop_insight_reports
        SET effects_phase = 'EFFECTS_COMPLETED',
            effects_owner = NULL,
            effects_lease_expires_at = NULL,
            effects_fencing_token = COALESCE(effects_fencing_token, 0),
            effects_applied_at = COALESCE(effects_applied_at, created_at)
        WHERE effects_status = 'APPLIED'
    """))
    bind.execute(sa.text("""
        UPDATE drop_insight_reports
        SET effects_phase = NULL
        WHERE effects_phase IS NOT NULL
          AND effects_phase NOT IN ('EXECUTION_STARTED', 'EFFECTS_COMPLETED')
    """))

    index_added = _INDEX not in _indexes(bind)
    if index_added:
        op.create_index(
            _INDEX,
            _TABLE,
            ["effects_status", "effects_lease_expires_at"],
            unique=False,
        )

    constraints = _constraints(bind)
    added_constraints = {
        name
        for name in (_STATUS_CONSTRAINT, _PHASE_CONSTRAINT)
        if name not in constraints
    }
    if added_constraints:
        with op.batch_alter_table(_TABLE) as batch:
            if _STATUS_CONSTRAINT in added_constraints:
                batch.create_check_constraint(
                    _STATUS_CONSTRAINT,
                    "effects_status IN ('PENDING', 'APPLYING', 'APPLIED')",
                )
            if _PHASE_CONSTRAINT in added_constraints:
                batch.create_check_constraint(
                    _PHASE_CONSTRAINT,
                    "effects_phase IS NULL OR effects_phase IN "
                    "('EXECUTION_STARTED', 'EFFECTS_COMPLETED')",
                )
    _record_ownership(
        bind,
        columns=added_columns,
        index=index_added,
        constraints=added_constraints,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in _tables(bind):
        return
    owned = _owned_objects(bind)
    if _INDEX in owned and _INDEX in _indexes(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    constraints = _constraints(bind)
    owned_columns = [
        name
        for name in reversed(_AUTHORITY_COLUMNS)
        if name in owned and name in _columns(bind)
    ]
    owned_constraints = {
        name
        for name in (_STATUS_CONSTRAINT, _PHASE_CONSTRAINT)
        if name in owned and name in constraints
    }
    if owned_columns or owned_constraints:
        with op.batch_alter_table(_TABLE) as batch:
            if _PHASE_CONSTRAINT in owned_constraints:
                batch.drop_constraint(_PHASE_CONSTRAINT, type_="check")
            if _STATUS_CONSTRAINT in owned_constraints:
                batch.drop_constraint(_STATUS_CONSTRAINT, type_="check")
            for name in owned_columns:
                batch.drop_column(name)
    if _OWNERSHIP_TABLE in _tables(bind):
        op.drop_table(_OWNERSHIP_TABLE)
