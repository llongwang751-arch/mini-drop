"""Add evidence-gated diagnostic skill evolution tables."""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0030"
down_revision = "20260824_0029"
branch_labels = None
depends_on = None

_TABLES = (
    "diagnostic_skills",
    "diagnostic_skill_evaluations",
    "diagnostic_skill_activations",
)
_OWNERSHIP_TABLE = "migration_20260824_0030_ownership"


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
    # The baseline migration creates all tables present in current SQLAlchemy
    # metadata. Guard every object so upgrading an old database and building a
    # fresh database are both safe.
    bind = op.get_bind()
    tables = _tables(bind)
    owned: set[str] = set()
    if "diagnostic_skills" not in tables:
        op.create_table(
            "diagnostic_skills",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("family_key", sa.String(length=256), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_diagnosis_ids_json", sa.JSON(), nullable=True),
        sa.Column("trigger_json", sa.JSON(), nullable=True),
        sa.Column("strategy_json", sa.JSON(), nullable=True),
        sa.Column("gate_metrics_json", sa.JSON(), nullable=True),
        sa.Column("parent_skill_id", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("family_key", "version", name="uq_diagnostic_skill_version"),
        sa.CheckConstraint(
            "status IN ('CANDIDATE', 'ACTIVE', 'QUARANTINED', 'RETIRED')",
            name="ck_diagnostic_skill_status",
        ),
        )
        owned.add("table:diagnostic_skills")
    for name, columns in (
        ("ix_diagnostic_skills_family_key", ["family_key"]),
        ("ix_diagnostic_skills_category", ["category"]),
        ("ix_diagnostic_skills_status", ["status"]),
        ("ix_diagnostic_skills_parent_skill_id", ["parent_skill_id"]),
    ):
        if name not in _indexes(bind, "diagnostic_skills"):
            op.create_index(name, "diagnostic_skills", columns)
            owned.add("index:" + name)

    if "diagnostic_skill_evaluations" not in tables:
        op.create_table(
            "diagnostic_skill_evaluations",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("case_kind", sa.String(length=32), nullable=False),
        sa.Column("diagnosis_id", sa.String(length=128), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["diagnostic_skills.id"]),
        )
        owned.add("table:diagnostic_skill_evaluations")
    for name, columns in (
        ("ix_diagnostic_skill_evaluations_skill_id", ["skill_id"]),
        ("ix_diagnostic_skill_evaluations_diagnosis_id", ["diagnosis_id"]),
    ):
        if name not in _indexes(bind, "diagnostic_skill_evaluations"):
            op.create_index(name, "diagnostic_skill_evaluations", columns)
            owned.add("index:" + name)

    if "diagnostic_skill_activations" not in tables:
        op.create_table(
            "diagnostic_skill_activations",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("diagnosis_id", sa.String(length=128), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("match_reason_json", sa.JSON(), nullable=True),
        sa.Column("baseline_tool", sa.String(length=128), nullable=False),
        sa.Column("selected_tool", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["diagnostic_skills.id"]),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["drop_insight_sessions.id"]),
        sa.UniqueConstraint("diagnosis_id", name="uq_diagnostic_skill_activation_diagnosis"),
        )
        owned.add("table:diagnostic_skill_activations")
    for name, columns in (
        ("ix_diagnostic_skill_activations_skill_id", ["skill_id"]),
        ("ix_diagnostic_skill_activations_diagnosis_id", ["diagnosis_id"]),
    ):
        if name not in _indexes(bind, "diagnostic_skill_activations"):
            op.create_index(name, "diagnostic_skill_activations", columns)
            owned.add("index:" + name)
    _record_ownership(bind, owned)


def downgrade() -> None:
    bind = op.get_bind()
    owned = _owned(bind)
    for table_name in reversed(_TABLES):
        if "table:" + table_name in owned and table_name in _tables(bind):
            op.drop_table(table_name)
    if _OWNERSHIP_TABLE in _tables(bind):
        op.drop_table(_OWNERSHIP_TABLE)
