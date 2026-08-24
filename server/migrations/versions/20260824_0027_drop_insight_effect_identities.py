"""Add semantic identities for Drop Insight report effects."""

from alembic import op
import sqlalchemy as sa

revision = "20260824_0027"
down_revision = "20260824_0026"
branch_labels = None
depends_on = None

_IDENTITIES = {
    "drop_insight_hypotheses": "uq_drop_insight_hypothesis_effect",
    "drop_insight_events": "uq_drop_insight_event_effect",
    "drop_insight_tool_calls": "uq_drop_insight_tool_call_effect",
}


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name: str) -> set[str]:
    return {
        item["name"] for item in sa.inspect(bind).get_columns(table_name)
    }


def _unique_identities(bind, table_name: str) -> set[tuple[str, ...]]:
    inspector = sa.inspect(bind)
    identities = {
        tuple(item.get("column_names") or [])
        for item in inspector.get_unique_constraints(table_name)
    }
    identities.update(
        tuple(item.get("column_names") or [])
        for item in inspector.get_indexes(table_name)
        if item.get("unique")
    )
    return identities


def _duplicate_effect(bind, table_name: str):
    return bind.execute(sa.text(f"""
        SELECT diagnosis_id, effect_key, COUNT(*) AS copies
        FROM {table_name}
        WHERE effect_key IS NOT NULL
        GROUP BY diagnosis_id, effect_key
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).mappings().first()


def upgrade() -> None:
    bind = op.get_bind()
    missing = sorted(set(_IDENTITIES) - _tables(bind))
    if missing:
        raise RuntimeError(
            "Drop Insight effect identity migration requires tables: "
            + ", ".join(missing)
        )

    for table_name in _IDENTITIES:
        if "effect_key" not in _columns(bind, table_name):
            op.add_column(
                table_name,
                sa.Column(
                    "effect_key",
                    sa.String(length=160),
                    nullable=True,
                ),
            )

        duplicate = _duplicate_effect(bind, table_name)
        if duplicate is not None:
            raise RuntimeError(
                "duplicate Drop Insight effect identity prevents migration: "
                f"table={table_name}, "
                f"diagnosis_id={duplicate['diagnosis_id']}, "
                f"effect_key={duplicate['effect_key']}, "
                f"count={duplicate['copies']}"
            )

        if ("diagnosis_id", "effect_key") not in _unique_identities(
            bind,
            table_name,
        ):
            op.create_index(
                _IDENTITIES[table_name],
                table_name,
                ["diagnosis_id", "effect_key"],
                unique=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    for table_name, index_name in reversed(tuple(_IDENTITIES.items())):
        if table_name not in tables:
            continue
        indexes = {
            item["name"] for item in sa.inspect(bind).get_indexes(table_name)
        }
        if index_name in indexes:
            op.drop_index(index_name, table_name=table_name)
        if "effect_key" in _columns(bind, table_name):
            with op.batch_alter_table(table_name) as batch:
                batch.drop_column("effect_key")
