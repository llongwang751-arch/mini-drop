"""Bind artifact metadata to immutable bytes with SHA-256 manifests."""

from alembic import op
import sqlalchemy as sa

revision = "20260802_0003"
down_revision = "20260801_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("artifacts")}
    with op.batch_alter_table("artifacts") as batch:
        if "sha256" not in existing:
            batch.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
        if "manifest_json" not in existing:
            batch.add_column(sa.Column("manifest_json", sa.JSON(), nullable=True))
        if "integrity_status" not in existing:
            batch.add_column(sa.Column(
                "integrity_status", sa.String(length=32), nullable=False,
                server_default="LEGACY_UNVERIFIED",
            ))
        if "integrity_reason" not in existing:
            batch.add_column(sa.Column(
                "integrity_reason", sa.Text(), nullable=False, server_default="",
            ))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("artifacts")}
    if "ix_artifacts_sha256" not in indexes:
        op.create_index("ix_artifacts_sha256", "artifacts", ["sha256"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("artifacts")}
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("artifacts")}
    with op.batch_alter_table("artifacts") as batch:
        if "ix_artifacts_sha256" in indexes:
            batch.drop_index("ix_artifacts_sha256")
        for column in ("integrity_reason", "integrity_status", "manifest_json", "sha256"):
            if column in existing:
                batch.drop_column(column)
