from app.db.url import normalize_database_url


def test_normalize_database_url_adds_psycopg_driver_to_plain_postgres_url() -> None:
    assert (
        normalize_database_url("postgresql://user:pass@example.com:5432/db")
        == "postgresql+psycopg://user:pass@example.com:5432/db"
    )


def test_normalize_database_url_keeps_existing_psycopg_driver() -> None:
    url = "postgresql+psycopg://user:pass@example.com:5432/db"

    assert normalize_database_url(url) == url


def test_normalize_database_url_leaves_non_postgres_urls_unchanged() -> None:
    sqlite_url = "sqlite:///tmp/test.db"

    assert normalize_database_url(sqlite_url) == sqlite_url
