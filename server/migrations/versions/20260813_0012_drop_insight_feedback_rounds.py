"""Add diagnosis rounds, decision source and human feedback."""

from alembic import op
import sqlalchemy as sa

revision = "20260813_0012"
down_revision = "20260807_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "drop_insight_hypotheses" in tables:
        columns = {row["name"] for row in sa.inspect(bind).get_columns("drop_insight_hypotheses")}
        additions = {
            "source": sa.Column("source", sa.String(32), nullable=False, server_default="DETERMINISTIC_RULE"),
            "round_index": sa.Column("round_index", sa.Integer(), nullable=False, server_default="1"),
            "parent_hypothesis_id": sa.Column("parent_hypothesis_id", sa.String(128), nullable=True),
            "generation_reason": sa.Column("generation_reason", sa.Text(), nullable=False, server_default=""),
        }
        for name, column in additions.items():
            if name not in columns:
                op.add_column("drop_insight_hypotheses", column)
    if "drop_insight_feedback" not in tables:
        op.create_table(
            "drop_insight_feedback",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("diagnosis_id", sa.String(128), sa.ForeignKey("drop_insight_sessions.id"), nullable=False),
            sa.Column("report_id", sa.String(128), sa.ForeignKey("drop_insight_reports.id"), nullable=True),
            sa.Column("hypothesis_id", sa.String(128), sa.ForeignKey("drop_insight_hypotheses.id"), nullable=True),
            sa.Column("feedback_label", sa.String(16), nullable=False),
            sa.Column("predicted_conclusion", sa.Text(), nullable=False, server_default=""),
            sa.Column("corrected_cause", sa.Text(), nullable=True),
            sa.Column("feedback_note", sa.Text(), nullable=True),
            sa.Column("requested_replan", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("revision_hypothesis_id", sa.String(128), nullable=True),
            sa.Column("created_by", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_drop_insight_feedback_diagnosis_id", "drop_insight_feedback", ["diagnosis_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "drop_insight_feedback" in tables:
        op.drop_table("drop_insight_feedback")
    if "drop_insight_hypotheses" in tables:
        columns = {row["name"] for row in sa.inspect(bind).get_columns("drop_insight_hypotheses")}
        removable = [
            name
            for name in ("generation_reason", "parent_hypothesis_id", "round_index", "source")
            if name in columns
        ]
        if removable:
            indexes = {
                row["name"]: set(row.get("column_names") or [])
                for row in sa.inspect(bind).get_indexes("drop_insight_hypotheses")
            }
            for index_name, indexed_columns in indexes.items():
                if indexed_columns.intersection(removable):
                    op.drop_index(index_name, table_name="drop_insight_hypotheses")
            # SQLite does not reliably support the sequence of ALTER TABLE DROP
            # COLUMN statements Alembic emits here. Batch mode rebuilds the table
            # and also remains valid on PostgreSQL, preserving reversible clean-host
            # migrations for both local tests and production deployments.
            with op.batch_alter_table("drop_insight_hypotheses") as batch_op:
                for name in removable:
                    batch_op.drop_column(name)
