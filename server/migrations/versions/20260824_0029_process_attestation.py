"""Add Agent-scoped process snapshot attestation."""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0029"
down_revision = "20260824_0028"
branch_labels = None
depends_on = None

_SNAPSHOT_TABLE = "process_candidate_snapshots"
_CANDIDATE_TABLE = "process_candidates"
_TASK_TABLE = "tasks"
_OWNERSHIP_TABLE = "migration_20260824_0029_ownership"
_TASK_COLUMNS = ("process_snapshot_id", "process_binding_json")
_TASK_SNAPSHOT_FK = "fk_tasks_process_snapshot_id"
_INDEXES = {
    _SNAPSHOT_TABLE: {"ix_process_snapshots_agent_received"},
    _CANDIDATE_TABLE: {"ix_process_candidates_snapshot_pid"},
    _TASK_TABLE: {"ix_tasks_process_snapshot_id"},
}


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_indexes(table)}


def _foreign_keys(bind, table: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table") or ""),
            tuple(item.get("referred_columns") or ()),
        )
        for item in sa.inspect(bind).get_foreign_keys(table)
    }


def _record_ownership(bind, names: set[str]) -> None:
    if _OWNERSHIP_TABLE not in _tables(bind):
        op.create_table(
            _OWNERSHIP_TABLE,
            sa.Column("object_name", sa.String(length=128), primary_key=True),
        )
    for name in sorted(names):
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


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if _TASK_TABLE not in tables or "agents" not in tables:
        raise RuntimeError("process attestation migration requires tasks and agents")
    owned: set[str] = set()

    if _SNAPSHOT_TABLE not in tables:
        op.create_table(
            _SNAPSHOT_TABLE,
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("agent_id", sa.String(length=128), nullable=False),
            sa.Column("generation", sa.BigInteger(), nullable=False),
            sa.Column("boot_id", sa.String(length=1024), nullable=False),
            sa.Column("observed_at_unix_ms", sa.BigInteger(), nullable=False),
            sa.Column("complete", sa.Boolean(), nullable=False),
            sa.Column("truncated", sa.Boolean(), nullable=False),
            sa.Column("error", sa.String(length=1024), nullable=False),
            sa.Column("state", sa.String(length=32), nullable=False),
            sa.Column("authoritative", sa.Boolean(), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.UniqueConstraint(
                "agent_id", "id", name="uq_process_snapshot_agent_id"
            ),
            sa.CheckConstraint(
                "state IN ('complete-empty', 'complete-populated', 'partial', "
                "'failed', 'truncated')",
                name="ck_process_snapshot_state",
            ),
            sa.CheckConstraint(
                "generation >= 0", name="ck_process_snapshot_generation"
            ),
        )
        owned.add("table:" + _SNAPSHOT_TABLE)

    if "ix_process_snapshots_agent_received" not in _indexes(bind, _SNAPSHOT_TABLE):
        op.create_index(
            "ix_process_snapshots_agent_received",
            _SNAPSHOT_TABLE,
            ["agent_id", "received_at", "id"],
        )
        owned.add("index:ix_process_snapshots_agent_received")

    if _CANDIDATE_TABLE not in tables:
        op.create_table(
            _CANDIDATE_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("snapshot_id", sa.String(length=128), nullable=False),
            sa.Column("agent_id", sa.String(length=128), nullable=False),
            sa.Column("pid", sa.Integer(), nullable=False),
            sa.Column("process_start_ticks", sa.BigInteger(), nullable=False),
            sa.Column("pid_namespace_inode", sa.BigInteger(), nullable=False),
            sa.Column("namespace_pid", sa.Integer(), nullable=False),
            sa.Column("executable_identity", sa.String(length=1024), nullable=False),
            sa.Column("comm", sa.String(length=1024), nullable=False),
            sa.Column("cgroup", sa.String(length=1024), nullable=False),
            sa.Column("service_hint", sa.String(length=1024), nullable=False),
            sa.Column("instance_hint", sa.String(length=1024), nullable=False),
            sa.Column("collector_capabilities", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(
                ["snapshot_id"], [_SNAPSHOT_TABLE + ".id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.UniqueConstraint(
                "snapshot_id", "pid", "process_start_ticks",
                "pid_namespace_inode", "namespace_pid", "executable_identity",
                name="uq_process_candidate_identity",
            ),
            sa.CheckConstraint("pid > 0", name="ck_process_candidate_pid"),
            sa.CheckConstraint(
                "process_start_ticks > 0",
                name="ck_process_candidate_start_ticks",
            ),
            sa.CheckConstraint(
                "pid_namespace_inode > 0",
                name="ck_process_candidate_namespace_inode",
            ),
            sa.CheckConstraint(
                "namespace_pid > 0", name="ck_process_candidate_namespace_pid"
            ),
        )
        owned.add("table:" + _CANDIDATE_TABLE)

    if "ix_process_candidates_snapshot_pid" not in _indexes(bind, _CANDIDATE_TABLE):
        op.create_index(
            "ix_process_candidates_snapshot_pid",
            _CANDIDATE_TABLE,
            ["snapshot_id", "pid"],
        )
        owned.add("index:ix_process_candidates_snapshot_pid")

    task_columns = _columns(bind, _TASK_TABLE)
    if "process_snapshot_id" not in task_columns:
        op.add_column(
            _TASK_TABLE,
            sa.Column("process_snapshot_id", sa.String(length=128), nullable=True),
        )
        owned.add("column:process_snapshot_id")
    if "process_binding_json" not in task_columns:
        op.add_column(
            _TASK_TABLE,
            sa.Column("process_binding_json", sa.JSON(), nullable=True),
        )
        owned.add("column:process_binding_json")
    task_snapshot_fk = (("process_snapshot_id",), _SNAPSHOT_TABLE, ("id",))
    if task_snapshot_fk not in _foreign_keys(bind, _TASK_TABLE):
        with op.batch_alter_table(_TASK_TABLE) as batch:
            batch.create_foreign_key(
                _TASK_SNAPSHOT_FK,
                _SNAPSHOT_TABLE,
                ["process_snapshot_id"],
                ["id"],
            )
        owned.add("foreign-key:" + _TASK_SNAPSHOT_FK)
    if "ix_tasks_process_snapshot_id" not in _indexes(bind, _TASK_TABLE):
        op.create_index(
            "ix_tasks_process_snapshot_id", _TASK_TABLE, ["process_snapshot_id"]
        )
        owned.add("index:ix_tasks_process_snapshot_id")
    _record_ownership(bind, owned)
    # SQLite batch table reconstruction may recreate previously-created tables
    # with their metadata definitions. Record ownership only after all batch
    # operations so that reconstruction cannot erase the ledger.


def downgrade() -> None:
    bind = op.get_bind()
    owned = _owned(bind)
    tables = _tables(bind)
    if _TASK_TABLE in tables:
        if (
            "index:ix_tasks_process_snapshot_id" in owned
            and "ix_tasks_process_snapshot_id" in _indexes(bind, _TASK_TABLE)
        ):
            op.drop_index("ix_tasks_process_snapshot_id", table_name=_TASK_TABLE)
        owned_task_fk = "foreign-key:" + _TASK_SNAPSHOT_FK in owned
        removable = [
            name for name in reversed(_TASK_COLUMNS)
            if "column:" + name in owned and name in _columns(bind, _TASK_TABLE)
        ]
        if removable or (
            owned_task_fk
            and (("process_snapshot_id",), _SNAPSHOT_TABLE, ("id",))
            in _foreign_keys(bind, _TASK_TABLE)
        ):
            with op.batch_alter_table(_TASK_TABLE) as batch:
                if owned_task_fk:
                    batch.drop_constraint(_TASK_SNAPSHOT_FK, type_="foreignkey")
                for name in removable:
                    batch.drop_column(name)
    if (
        "index:ix_process_candidates_snapshot_pid" in owned
        and "table:" + _CANDIDATE_TABLE not in owned
        and _CANDIDATE_TABLE in _tables(bind)
        and "ix_process_candidates_snapshot_pid" in _indexes(bind, _CANDIDATE_TABLE)
    ):
        op.drop_index(
            "ix_process_candidates_snapshot_pid", table_name=_CANDIDATE_TABLE
        )
    if (
        "index:ix_process_snapshots_agent_received" in owned
        and "table:" + _SNAPSHOT_TABLE not in owned
        and _SNAPSHOT_TABLE in _tables(bind)
        and "ix_process_snapshots_agent_received" in _indexes(bind, _SNAPSHOT_TABLE)
    ):
        op.drop_index(
            "ix_process_snapshots_agent_received", table_name=_SNAPSHOT_TABLE
        )
    if (
        "table:" + _CANDIDATE_TABLE in owned
        and _CANDIDATE_TABLE in _tables(bind)
    ):
        op.drop_table(_CANDIDATE_TABLE)
    if (
        "table:" + _SNAPSHOT_TABLE in owned
        and _SNAPSHOT_TABLE in _tables(bind)
    ):
        op.drop_table(_SNAPSHOT_TABLE)
    if _OWNERSHIP_TABLE in _tables(bind):
        op.drop_table(_OWNERSHIP_TABLE)
