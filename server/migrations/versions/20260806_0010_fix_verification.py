"""Fix-verification loop: before/after task evidence -> VERIFIED/REJECTED."""

from alembic import op
import sqlalchemy as sa

revision = "20260806_0010"
down_revision = "20260806_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "fix_verifications" not in tables:
        op.create_table(
            "fix_verifications",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("diagnosis_id", sa.String(length=128), nullable=False),
            sa.Column("fix_summary", sa.Text(), nullable=True),
            sa.Column("before_task_id", sa.String(length=128), nullable=False),
            sa.Column("after_task_id", sa.String(length=128), nullable=False),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column("before_hotspot_json", sa.JSON(), nullable=True),
            sa.Column("after_hotspot_json", sa.JSON(), nullable=True),
            sa.Column("comparison_json", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "fix_verifications" in tables:
        indexes = {
            item["name"] for item in sa.inspect(bind).get_indexes("fix_verifications")
        }
        if "ix_fix_verification_diagnosis" not in indexes:
            op.create_index(
                "ix_fix_verification_diagnosis", "fix_verifications", ["diagnosis_id"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "fix_verifications" in tables:
        op.drop_index("ix_fix_verification_diagnosis", table_name="fix_verifications")
        op.drop_table("fix_verifications")
