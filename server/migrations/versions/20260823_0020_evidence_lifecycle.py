"""Persist authoritative Evidence lifecycle and review state."""

from alembic import op
import sqlalchemy as sa

revision = "20260823_0020"
down_revision = "20260823_0019"
branch_labels = None
depends_on = None


def _require_evidence_tables(bind) -> None:
    tables = set(sa.inspect(bind).get_table_names())
    missing = {"diagnosis_sessions", "diagnosis_evidence"} - tables
    if missing:
        raise RuntimeError(
            "cannot migrate Evidence lifecycle: missing " + ", ".join(sorted(missing))
        )


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    existing = {item["name"] for item in sa.inspect(bind).get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    _require_evidence_tables(bind)

    _add_column_if_missing(
        "diagnosis_evidence",
        sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="ACTIVE"),
    )
    _add_column_if_missing(
        "diagnosis_evidence",
        sa.Column("trust_status", sa.String(32), nullable=False, server_default="UNREVIEWED"),
    )
    _add_column_if_missing(
        "diagnosis_evidence",
        sa.Column("superseded_by", sa.String(128), nullable=True),
    )
    _add_column_if_missing(
        "diagnosis_evidence",
        sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "diagnosis_evidence",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "diagnosis_evidence",
        sa.Column("reviewer_id", sa.String(128), nullable=True),
    )

    tables = set(sa.inspect(bind).get_table_names())
    if "diagnosis_evidence_reviews" not in tables:
        op.create_table(
            "diagnosis_evidence_reviews",
            sa.Column("id", sa.String(200), primary_key=True),
            sa.Column("diagnosis_id", sa.String(128), nullable=False),
            sa.Column("evidence_id", sa.String(128), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("lifecycle_status", sa.String(32), nullable=False),
            sa.Column("trust_status", sa.String(32), nullable=False),
            sa.Column("superseded_by", sa.String(128), nullable=True),
            sa.Column("reviewer_id", sa.String(128), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
            sa.ForeignKeyConstraint(["evidence_id"], ["diagnosis_evidence.id"]),
            sa.ForeignKeyConstraint(["superseded_by"], ["diagnosis_evidence.id"]),
            sa.UniqueConstraint(
                "evidence_id", "revision",
                name="uq_diagnosis_evidence_review_revision",
            ),
        )
        op.create_index(
            "ix_diagnosis_evidence_reviews_diagnosis_id",
            "diagnosis_evidence_reviews",
            ["diagnosis_id"],
        )
        op.create_index(
            "ix_diagnosis_evidence_reviews_evidence_id",
            "diagnosis_evidence_reviews",
            ["evidence_id"],
        )

    foreign_keys = sa.inspect(bind).get_foreign_keys("diagnosis_evidence")
    has_supersession_fk = any(
        item.get("referred_table") == "diagnosis_evidence"
        and item.get("constrained_columns") == ["superseded_by"]
        for item in foreign_keys
    )
    if not has_supersession_fk and bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_diagnosis_evidence_superseded_by",
            "diagnosis_evidence",
            "diagnosis_evidence",
            ["superseded_by"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "diagnosis_evidence_reviews" in tables:
        op.drop_table("diagnosis_evidence_reviews")

    if "diagnosis_evidence" not in tables:
        return
    existing = {item["name"] for item in sa.inspect(bind).get_columns("diagnosis_evidence")}
    removable = [
        name
        for name in (
            "reviewer_id",
            "reviewed_at",
            "review_revision",
            "superseded_by",
            "trust_status",
            "lifecycle_status",
        )
        if name in existing
    ]
    if removable:
        with op.batch_alter_table("diagnosis_evidence") as batch:
            for name in removable:
                batch.drop_column(name)
