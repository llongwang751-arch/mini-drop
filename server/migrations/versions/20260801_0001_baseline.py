"""Establish the versioned Mini-Drop schema baseline.

The revision is idempotent and can adopt databases created by the legacy
``Base.metadata.create_all`` startup path without deleting existing data.
"""

from alembic import op
from server.app.models import Base

revision = "20260801_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # An adopted baseline may contain production data. Later revisions are
    # reversible, while crossing the baseline is intentionally data-safe.
    pass
