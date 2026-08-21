"""Add delivery lifecycle fields to the diagnosis artifact outbox."""

from alembic import op
import sqlalchemy as sa

revision = "20260821_0016"
down_revision = "20260821_0015"
branch_labels = None
depends_on = None

_TABLE = "diagnosis_artifact_outbox"
_INDEXES = (
    ("ix_diagnosis_artifact_outbox_status", ["status"]),
    (
        "ix_diagnosis_artifact_outbox_due",
        ["status", "next_attempt_at", "created_at"],
    ),
    (
        "ix_diagnosis_artifact_outbox_lease_expiry",
        ["worker_lease_expires_at"],
    ),
)


def _columns(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        raise RuntimeError(
            "cannot add artifact outbox lifecycle: "
            "diagnosis_artifact_outbox is missing"
        )

    existing = _columns(bind)
    additions = (
        ("attempts", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")),
        ("next_attempt_at", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)),
        ("worker_lease_owner", sa.Column("worker_lease_owner", sa.String(128), nullable=True)),
        ("worker_lease_expires_at", sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True)),
        ("last_error", sa.Column("last_error", sa.Text(), nullable=True)),
        ("published_at", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)),
    )
    for name, column in additions:
        if name not in existing:
            op.add_column(_TABLE, column)

    bind.execute(sa.text(
        "UPDATE diagnosis_artifact_outbox "
        "SET attempts = COALESCE(attempts, 0), "
        "next_attempt_at = COALESCE(next_attempt_at, created_at)"
    ))
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column("next_attempt_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    else:
        op.alter_column(_TABLE, "next_attempt_at", nullable=False)

    inspector = sa.inspect(bind)
    existing_indexes = {item["name"] for item in inspector.get_indexes(_TABLE)}
    for name, columns in _INDEXES:
        if name not in existing_indexes:
            op.create_index(name, _TABLE, columns)


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in set(sa.inspect(bind).get_table_names()):
        return

    existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)}
    for name, _columns_ in reversed(_INDEXES):
        if name in existing_indexes:
            op.drop_index(name, table_name=_TABLE)

    existing = _columns(bind)
    removable = [
        name
        for name in (
            "published_at",
            "last_error",
            "worker_lease_expires_at",
            "worker_lease_owner",
            "next_attempt_at",
            "attempts",
        )
        if name in existing
    ]
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            for name in removable:
                batch.drop_column(name)
    else:
        for name in removable:
            op.drop_column(_TABLE, name)
