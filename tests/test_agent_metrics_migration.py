import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

def test_metrics_migration_preserves_existing_agent_and_null_is_not_zero():
    path = Path(__file__).resolve().parents[1] / "server/migrations/versions/20260910_0008_agent_latest_metrics.py"
    spec = importlib.util.spec_from_file_location("metrics_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE agents (id TEXT PRIMARY KEY, hostname TEXT)"))
        conn.execute(text("INSERT INTO agents VALUES ('a', 'original')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
        assert "latest_metrics" in {c["name"] for c in inspect(conn).get_columns("agents")}
        assert conn.execute(text("SELECT hostname, latest_metrics FROM agents")).one() == ("original", None)
