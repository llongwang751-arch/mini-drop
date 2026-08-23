"""Bind immutable Artifact and analyzer provenance to Evidence Snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "20260823_0021"
down_revision = "20260823_0020"
branch_labels = None
depends_on = None


def _snapshot_columns(bind) -> set[str]:
    tables = set(sa.inspect(bind).get_table_names())
    if "diagnosis_evidence_snapshots" not in tables:
        raise RuntimeError(
            "cannot migrate Evidence Snapshot provenance: missing "
            "diagnosis_evidence_snapshots"
        )
    return {
        item["name"]
        for item in sa.inspect(bind).get_columns("diagnosis_evidence_snapshots")
    }


def upgrade() -> None:
    bind = op.get_bind()
    existing = _snapshot_columns(bind)
    if "artifact_provenance_json" not in existing:
        op.add_column(
            "diagnosis_evidence_snapshots",
            sa.Column("artifact_provenance_json", sa.JSON(), nullable=True),
        )
    if "analysis_provenance_json" not in existing:
        op.add_column(
            "diagnosis_evidence_snapshots",
            sa.Column("analysis_provenance_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _snapshot_columns(bind)
    removable = [
        name
        for name in (
            "analysis_provenance_json",
            "artifact_provenance_json",
        )
        if name in existing
    ]
    if removable:
        with op.batch_alter_table("diagnosis_evidence_snapshots") as batch:
            for name in removable:
                batch.drop_column(name)
