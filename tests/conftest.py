import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.config import get_settings
from app.db.url import normalize_database_url


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or get_settings().database_url
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        pytest.fail("Repository tests must run against PostgreSQL, not SQLite.")
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Engine:
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    engine = create_engine(database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(engine: Engine) -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")


@pytest.fixture()
def db_session(engine: Engine) -> Session:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE sync_source_results, sync_runs, sync_checkpoints, "
                "normalized_records, normalized_financial_records, "
                "revenue_status_allowlist RESTART IDENTITY CASCADE"
            )
        )

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as session:
        yield session
        session.rollback()
