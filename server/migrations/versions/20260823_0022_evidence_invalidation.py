"""Persist conclusion invalidation and artifact revocation facts."""

from alembic import op
import sqlalchemy as sa

revision = "20260823_0022"
down_revision = "20260823_0021"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "diagnosis_conclusion_invalidations" not in tables:
        op.create_table(
            "diagnosis_conclusion_invalidations",
            sa.Column("id", sa.String(length=128), nullable=False),
            sa.Column("diagnosis_id", sa.String(length=128), nullable=False),
            sa.Column("conclusion_hash", sa.String(length=80), nullable=False),
            sa.Column("evidence_id", sa.String(length=128), nullable=False),
            sa.Column("review_revision", sa.Integer(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
            sa.ForeignKeyConstraint(["evidence_id"], ["diagnosis_evidence.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "diagnosis_id",
                "conclusion_hash",
                "evidence_id",
                "review_revision",
                name="uq_diagnosis_conclusion_invalidation_identity",
            ),
        )
    indexes = _indexes(bind, "diagnosis_conclusion_invalidations")
    if "ix_diagnosis_conclusion_invalidations_diagnosis_id" not in indexes:
        op.create_index(
            "ix_diagnosis_conclusion_invalidations_diagnosis_id",
            "diagnosis_conclusion_invalidations",
            ["diagnosis_id"],
        )
    if "ix_diagnosis_conclusion_invalidations_conclusion_hash" not in indexes:
        op.create_index(
            "ix_diagnosis_conclusion_invalidations_conclusion_hash",
            "diagnosis_conclusion_invalidations",
            ["conclusion_hash"],
        )

    tables = _tables(bind)
    if "diagnosis_revalidation_requests" not in tables:
        op.create_table(
            "diagnosis_revalidation_requests",
            sa.Column("id", sa.String(length=128), nullable=False),
            sa.Column("diagnosis_id", sa.String(length=128), nullable=False),
            sa.Column("conclusion_hash", sa.String(length=80), nullable=False),
            sa.Column("evidence_id", sa.String(length=128), nullable=False),
            sa.Column("review_revision", sa.Integer(), nullable=False),
            sa.Column(
                "status", sa.String(length=32), nullable=False, server_default="PENDING"
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
            sa.ForeignKeyConstraint(["evidence_id"], ["diagnosis_evidence.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "diagnosis_id",
                "conclusion_hash",
                "evidence_id",
                "review_revision",
                name="uq_diagnosis_revalidation_request_identity",
            ),
        )
    indexes = _indexes(bind, "diagnosis_revalidation_requests")
    if "ix_diagnosis_revalidation_requests_diagnosis_id" not in indexes:
        op.create_index(
            "ix_diagnosis_revalidation_requests_diagnosis_id",
            "diagnosis_revalidation_requests",
            ["diagnosis_id"],
        )
    if "ix_diagnosis_revalidation_requests_status" not in indexes:
        op.create_index(
            "ix_diagnosis_revalidation_requests_status",
            "diagnosis_revalidation_requests",
            ["status"],
        )

    tables = _tables(bind)
    if "diagnosis_artifact_revocations" not in tables:
        op.create_table(
            "diagnosis_artifact_revocations",
            sa.Column("id", sa.String(length=128), nullable=False),
            sa.Column("diagnosis_id", sa.String(length=128), nullable=False),
            sa.Column("artifact_id", sa.String(length=128), nullable=False),
            sa.Column("artifact_hash", sa.String(length=80), nullable=False),
            sa.Column("conclusion_hash", sa.String(length=80), nullable=False),
            sa.Column("evidence_id", sa.String(length=128), nullable=False),
            sa.Column("review_revision", sa.Integer(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnosis_sessions.id"]),
            sa.ForeignKeyConstraint(
                ["artifact_id"], ["frozen_diagnosis_artifacts.id"]
            ),
            sa.ForeignKeyConstraint(["evidence_id"], ["diagnosis_evidence.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "artifact_id",
                "evidence_id",
                "review_revision",
                name="uq_diagnosis_artifact_revocation_identity",
            ),
        )
    indexes = _indexes(bind, "diagnosis_artifact_revocations")
    if "ix_diagnosis_artifact_revocations_diagnosis_id" not in indexes:
        op.create_index(
            "ix_diagnosis_artifact_revocations_diagnosis_id",
            "diagnosis_artifact_revocations",
            ["diagnosis_id"],
        )
    if "ix_diagnosis_artifact_revocations_artifact_id" not in indexes:
        op.create_index(
            "ix_diagnosis_artifact_revocations_artifact_id",
            "diagnosis_artifact_revocations",
            ["artifact_id"],
        )

    tables = _tables(bind)
    if "diagnosis_artifact_revocation_outbox" not in tables:
        op.create_table(
            "diagnosis_artifact_revocation_outbox",
            sa.Column("id", sa.String(length=160), nullable=False),
            sa.Column("revocation_id", sa.String(length=128), nullable=False),
            sa.Column(
                "status", sa.String(length=32), nullable=False, server_default="PENDING"
            ),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("worker_lease_owner", sa.String(length=128), nullable=True),
            sa.Column(
                "worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["revocation_id"], ["diagnosis_artifact_revocations.id"]
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("revocation_id"),
        )
    indexes = _indexes(bind, "diagnosis_artifact_revocation_outbox")
    if "ix_diagnosis_artifact_revocation_outbox_status" not in indexes:
        op.create_index(
            "ix_diagnosis_artifact_revocation_outbox_status",
            "diagnosis_artifact_revocation_outbox",
            ["status"],
        )
    if "ix_diagnosis_artifact_revocation_outbox_due" not in indexes:
        op.create_index(
            "ix_diagnosis_artifact_revocation_outbox_due",
            "diagnosis_artifact_revocation_outbox",
            ["status", "next_attempt_at"],
        )
    if "ix_diagnosis_artifact_revocation_outbox_lease_recovery" not in indexes:
        op.create_index(
            "ix_diagnosis_artifact_revocation_outbox_lease_recovery",
            "diagnosis_artifact_revocation_outbox",
            ["status", "worker_lease_expires_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "diagnosis_artifact_revocation_outbox",
        "diagnosis_artifact_revocations",
        "diagnosis_revalidation_requests",
        "diagnosis_conclusion_invalidations",
    ):
        if table_name in _tables(bind):
            op.drop_table(table_name)
