from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.url import normalize_database_url

settings = get_settings()

engine = create_engine(
    normalize_database_url(settings.database_url),
    pool_pre_ping=settings.database_pool_pre_ping,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
