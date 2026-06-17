from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Integrity Sync API"
    app_env: str = "local"
    database_url: str = (
        "postgresql+psycopg://ai_integrity:ai_integrity@localhost:5433/ai_integrity"
    )
    debug_sync_tools_enabled: bool = False
    database_pool_pre_ping: bool = True
    hubspot_access_token: str | None = None
    google_calendar_access_token: str | None = None
    google_calendar_api_key: str | None = None
    stripe_secret_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
