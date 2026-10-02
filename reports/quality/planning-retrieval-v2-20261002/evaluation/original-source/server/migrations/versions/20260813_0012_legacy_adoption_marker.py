"""Adopt databases created by the legacy Mini-Drop migration chain.

The historical migrations were retired with the compatibility stack.  Existing
installations can still legitimately report this revision, so keep the marker
as a no-op bridge into the current runtime baseline.
"""


revision = "20260813_0012"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
