"""Persist the latest native Agent heartbeat metrics without fabricating zero."""
from alembic import op
import sqlalchemy as sa

revision = "20260910_0008"
down_revision = "20260908_0007"
branch_labels = None
depends_on = None

def upgrade():
    # Baseline installation uses current ORM metadata, so tolerate an existing
    # column when upgrading a newly created database through the revision chain.
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("agents")}
    if "latest_metrics" not in columns:
        op.add_column("agents", sa.Column("latest_metrics", sa.JSON(), nullable=True))

def downgrade():
    op.drop_column("agents", "latest_metrics")
