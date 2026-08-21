"""Create immutable diagnosis artifacts and evaluator records."""

from alembic import op
import sqlalchemy as sa

revision = "20260821_0014"
down_revision = "20260821_0013"
branch_labels = None
depends_on = None


def _create_table_if_missing(bind, name: str, columns, constraints=()):
    tables = set(sa.inspect(bind).get_table_names())
    if name not in tables:
        op.create_table(name, *columns, *constraints)
        return
    required = {column.name for column in columns if isinstance(column, sa.Column)}
    existing = {
        item["name"] for item in sa.inspect(bind).get_columns(name)
    }
    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(
            f"existing {name} table is incomplete; missing columns: "
            + ", ".join(missing)
        )


def _require_foreign_keys(bind, table: str, expected: set[tuple[str, str]]) -> None:
    actual = {
        (item["constrained_columns"][0], item["referred_table"])
        for item in sa.inspect(bind).get_foreign_keys(table)
        if len(item.get("constrained_columns") or []) == 1
    }
    missing = sorted(expected - actual)
    if missing:
        rendered = ", ".join(
            f"{column}->{parent}" for column, parent in missing
        )
        raise RuntimeError(
            f"existing {table} table is incomplete; missing foreign keys: {rendered}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _create_table_if_missing(bind, "frozen_diagnosis_artifacts", [
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("diagnosis_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("terminal_status", sa.String(32), nullable=False),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("artifact_hash", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
        sa.UniqueConstraint("diagnosis_id", name="uq_frozen_diagnosis_artifact_diagnosis"),
    ])
    _create_table_if_missing(bind, "diagnosis_artifact_outbox", [
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("diagnosis_id", sa.String(128), nullable=False),
        sa.Column("artifact_id", sa.String(128), nullable=False, unique=True),
        sa.Column("artifact_hash", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["frozen_diagnosis_artifacts.id"]),
    ])
    _create_table_if_missing(bind, "diagnosis_artifact_evaluations", [
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("artifact_id", sa.String(128), nullable=False),
        sa.Column("artifact_hash", sa.String(80), nullable=False),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["frozen_diagnosis_artifacts.id"]),
        sa.UniqueConstraint(
            "artifact_id", "artifact_hash", "evaluator_version",
            name="uq_diagnosis_artifact_evaluation_identity",
        ),
    ])
    _require_foreign_keys(
        bind,
        "frozen_diagnosis_artifacts",
        {("diagnosis_id", "diagnosis_sessions")},
    )
    _require_foreign_keys(
        bind,
        "diagnosis_artifact_outbox",
        {
            ("diagnosis_id", "diagnosis_sessions"),
            ("artifact_id", "frozen_diagnosis_artifacts"),
        },
    )
    _require_foreign_keys(
        bind,
        "diagnosis_artifact_evaluations",
        {("artifact_id", "frozen_diagnosis_artifacts")},
    )
    inspector = sa.inspect(bind)
    for table, index, column in (
        ("frozen_diagnosis_artifacts", "ix_frozen_diagnosis_artifacts_diagnosis_id", "diagnosis_id"),
        ("diagnosis_artifact_outbox", "ix_diagnosis_artifact_outbox_diagnosis_id", "diagnosis_id"),
        ("diagnosis_artifact_evaluations", "ix_diagnosis_artifact_evaluations_artifact_id", "artifact_id"),
    ):
        if index not in {item["name"] for item in inspector.get_indexes(table)}:
            op.create_index(index, table, [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in (
        "diagnosis_artifact_evaluations",
        "diagnosis_artifact_outbox",
        "frozen_diagnosis_artifacts",
    ):
        if table in set(inspector.get_table_names()):
            op.drop_table(table)
