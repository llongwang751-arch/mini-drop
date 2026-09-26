"""Shared test fixtures. The PostgreSQL fixture is CI-wired: it must skip
locally and fail the dedicated CI job if it runs zero or skipped cases."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from server.app.drop_insight import service as drop_insight_service
from server.app.models import Base

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


def _postgres_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture
def postgres_sessions(monkeypatch):
    if not _postgres_enabled(os.getenv("RUN_POSTGRES_TESTS")):
        pytest.skip("set RUN_POSTGRES_TESTS=1 to run PostgreSQL integration tests")
    raw_url = os.getenv("MINI_DROP_TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("MINI_DROP_TEST_POSTGRES_URL is not configured")
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("MINI_DROP_TEST_POSTGRES_URL must use PostgreSQL")
    if not url.database or "test" not in url.database.lower():
        pytest.fail(
            "MINI_DROP_TEST_POSTGRES_URL must name a dedicated test database"
        )

    schema = f"mini_drop_test_{uuid4().hex}"
    admin_engine = create_engine(url, pool_pre_ping=True)
    test_engine = None
    schema_created = False
    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        schema_created = True
        test_engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "options": (
                    f"-csearch_path={schema} "
                    "-cstatement_timeout=5000"
                )
            },
        )
        Base.metadata.create_all(test_engine)
        factory = sessionmaker(
            bind=test_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        monkeypatch.setattr(drop_insight_service, "new_session", factory)
        yield factory
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()
