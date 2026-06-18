def normalize_database_url(database_url: str) -> str:
    """Use psycopg v3 when a Postgres URL omits an explicit SQLAlchemy driver."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url
