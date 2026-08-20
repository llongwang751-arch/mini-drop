"""Persist deterministic Claim-Evidence verification results."""

from alembic import op
import sqlalchemy as sa

revision = "20260805_0004"
down_revision = "20260802_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("drop_insight_reports")
    }
    with op.batch_alter_table("drop_insight_reports") as batch:
        if "claims_json" not in existing:
            batch.add_column(sa.Column("claims_json", sa.JSON(), nullable=True))
        if "verification_json" not in existing:
            batch.add_column(sa.Column("verification_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("drop_insight_reports")
    }
    with op.batch_alter_table("drop_insight_reports") as batch:
        if "verification_json" in existing:
            batch.drop_column("verification_json")
        if "claims_json" in existing:
            batch.drop_column("claims_json")
