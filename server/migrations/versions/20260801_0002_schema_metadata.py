"""Add application schema metadata and a reversible migration boundary."""

from alembic import op
import sqlalchemy as sa

revision = "20260801_0002"
down_revision = "20260801_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_schema_metadata",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.bulk_insert(
        sa.table(
            "platform_schema_metadata",
            sa.column("key", sa.String()),
            sa.column("value", sa.Text()),
        ),
        [{"key": "migration_policy", "value": "alembic-versioned"}],
    )


def downgrade() -> None:
    op.drop_table("platform_schema_metadata")
