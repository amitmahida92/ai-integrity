from app.core.config import Settings
from app.sources.google_calendar import GoogleCalendarEventsAdapter, GoogleCalendarHttpClient
from app.sync.providers import HttpProviderAdapterFactory


def test_google_provider_factory_uses_refresh_token_oauth_settings() -> None:
    adapter = HttpProviderAdapterFactory(
        Settings(
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_refresh_token="refresh-token",
            google_calendar_id="calendar-id",
        )
    ).create("google_calendar")

    assert isinstance(adapter, GoogleCalendarEventsAdapter)
    assert isinstance(adapter.client, GoogleCalendarHttpClient)
    assert adapter.default_calendar_id == "calendar-id"
