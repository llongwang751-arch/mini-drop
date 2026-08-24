"""Add durable Drop Insight target discovery authority."""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0031"
down_revision = "20260824_0030"
branch_labels = None
depends_on = None

_DISCOVERY_TABLE = "drop_insight_target_discoveries"
_BINDING_TABLE = "drop_insight_target_bindings"
_OWNERSHIP_TABLE = "migration_20260824_0031_ownership"


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}


def _record_ownership(bind, names: set[str]) -> None:
    if not names:
        return
    if _OWNERSHIP_TABLE not in _tables(bind):
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
    if _OWNERSHIP_TABLE not in _tables(bind):
        return set()
    return {
        str(row[0])
        for row in bind.execute(sa.text(f"SELECT object_name FROM {_OWNERSHIP_TABLE}"))
    }


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    required = {"drop_insight_sessions", "agents", "process_candidate_snapshots"}
    if not required.issubset(tables):
        raise RuntimeError("target discovery migration requires diagnosis and process authority tables")
    owned: set[str] = set()

    if _DISCOVERY_TABLE not in tables:
        op.create_table(
            _DISCOVERY_TABLE,
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("diagnosis_id", sa.String(length=128), nullable=False),
            sa.Column("diagnosis_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("service_filter", sa.String(length=128), nullable=True),
            sa.Column("environment_filter", sa.String(length=64), nullable=True),
            sa.Column("snapshot_state_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["diagnosis_id"], ["drop_insight_sessions.id"], ondelete="CASCADE"
            ),
            sa.CheckConstraint(
                "status IN ('READY', 'AMBIGUOUS', 'EMPTY', 'STALE', "
                "'UNAVAILABLE', 'TRUNCATED', 'INVALIDATED')",
                name="ck_drop_insight_target_discovery_status",
            ),
        )
        owned.add("table:" + _DISCOVERY_TABLE)
    if "ix_drop_insight_target_discovery_scope" not in _indexes(bind, _DISCOVERY_TABLE):
        op.create_index(
            "ix_drop_insight_target_discovery_scope",
            _DISCOVERY_TABLE,
            ["diagnosis_id", "diagnosis_version", "created_at"],
        )
        owned.add("index:ix_drop_insight_target_discovery_scope")

    if _BINDING_TABLE not in tables:
        op.create_table(
            _BINDING_TABLE,
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("discovery_id", sa.String(length=128), nullable=False),
            sa.Column("agent_id", sa.String(length=128), nullable=False),
            sa.Column("process_snapshot_id", sa.String(length=128), nullable=False),
            sa.Column("pid", sa.Integer(), nullable=False),
            sa.Column("process_binding_json", sa.JSON(), nullable=False),
            sa.Column("display_json", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(
                ["discovery_id"], [_DISCOVERY_TABLE + ".id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(
                ["process_snapshot_id"], ["process_candidate_snapshots.id"]
            ),
            sa.UniqueConstraint(
                "discovery_id", "id", name="uq_drop_insight_target_binding_membership"
            ),
        )
        owned.add("table:" + _BINDING_TABLE)
    if "ix_drop_insight_target_bindings_discovery" not in _indexes(bind, _BINDING_TABLE):
        op.create_index(
            "ix_drop_insight_target_bindings_discovery",
            _BINDING_TABLE,
            ["discovery_id", "id"],
        )
        owned.add("index:ix_drop_insight_target_bindings_discovery")
    _record_ownership(bind, owned)


def downgrade() -> None:
    bind = op.get_bind()
    owned = _owned(bind)
    tables = _tables(bind)
    for index_name, table_name in (
        ("ix_drop_insight_target_bindings_discovery", _BINDING_TABLE),
        ("ix_drop_insight_target_discovery_scope", _DISCOVERY_TABLE),
    ):
        if (
            "index:" + index_name in owned
            and "table:" + table_name not in owned
            and table_name in tables
            and index_name in _indexes(bind, table_name)
        ):
            op.drop_index(index_name, table_name=table_name)
    for table_name in (_BINDING_TABLE, _DISCOVERY_TABLE):
        if "table:" + table_name in owned and table_name in _tables(bind):
            op.drop_table(table_name)
    if _OWNERSHIP_TABLE in _tables(bind):
        op.drop_table(_OWNERSHIP_TABLE)
