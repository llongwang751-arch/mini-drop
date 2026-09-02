from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from server.app.models import Base


def _alembic(tmp_path: Path, command: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *command.split()],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_current_baseline_creates_only_runtime_models(tmp_path: Path) -> None:
    _alembic(tmp_path, "upgrade head")
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'migration.db').as_posix()}",
        poolclass=NullPool,
    )
    actual = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert actual == set(Base.metadata.tables)
    assert "task_upload_authorizations" in actual
    assert "drop_insight_sessions" in actual
    assert "diagnostic_skills" in actual
    assert "updated_at" in {
        item["name"] for item in inspect(engine).get_columns("tasks")
    }
    assert "diagnosis_sessions" not in actual
    assert "agent_runtime_turns" not in actual
    engine.dispose()


def test_current_baseline_downgrade_is_clean(tmp_path: Path) -> None:
    _alembic(tmp_path, "upgrade head")
    _alembic(tmp_path, "downgrade base")
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'migration.db').as_posix()}",
        poolclass=NullPool,
    )
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def test_current_baseline_adopts_legacy_revision(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_sentinel (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    _alembic(tmp_path, "stamp 20260813_0012")
    _alembic(tmp_path, "upgrade head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}", poolclass=NullPool)
    tables = set(inspect(engine).get_table_names())
    assert "legacy_sentinel" in tables
    assert "diagnostic_skills" in tables
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260901_0005"
    engine.dispose()
