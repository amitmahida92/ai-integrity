from typing import Any, Protocol

from app.core.config import Settings
from app.sources.exceptions import ProviderClientError
from app.sources.google_calendar import (
    GOOGLE_PROVIDER,
    GoogleCalendarEventsAdapter,
    GoogleCalendarHttpClient,
)
from app.sources.hubspot import HUBSPOT_PROVIDER, HubSpotContactsAdapter, HubSpotHttpClient
from app.sources.stripe import STRIPE_PROVIDER, StripeHttpClient, StripePaymentIntentsAdapter
from app.sources.types import FetchResult
from app.sync.constants import ProviderName


class SourceAdapter(Protocol):
    def fetch_full(self, checkpoint_data: dict[str, Any] | None = None) -> FetchResult:
        ...

    def fetch_incremental(self, checkpoint_data: dict[str, Any]) -> FetchResult:
        ...


class ProviderAdapterFactory(Protocol):
    def create(self, provider: str) -> SourceAdapter:
        ...


class HttpProviderAdapterFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create(self, provider: str) -> SourceAdapter:
        if provider == ProviderName.HUBSPOT.value:
            if not self.settings.hubspot_access_token:
                raise ProviderClientError("HubSpot credentials are not configured")
            return HubSpotContactsAdapter(
                client=HubSpotHttpClient(access_token=self.settings.hubspot_access_token)
            )

        if provider == ProviderName.GOOGLE_CALENDAR.value:
            if (
                not self.settings.google_calendar_access_token
                and not self.settings.google_calendar_api_key
            ):
                raise ProviderClientError("Google Calendar credentials are not configured")
            return GoogleCalendarEventsAdapter(
                client=GoogleCalendarHttpClient(
                    access_token=self.settings.google_calendar_access_token,
                    api_key=self.settings.google_calendar_api_key,
                )
            )

        if provider == ProviderName.STRIPE.value:
            if not self.settings.stripe_secret_key:
                raise ProviderClientError("Stripe credentials are not configured")
            return StripePaymentIntentsAdapter(
                client=StripeHttpClient(secret_key=self.settings.stripe_secret_key)
            )

        raise ProviderClientError(f"Unsupported provider: {provider}")


DEFAULT_ENTITY_TYPES = {
    HUBSPOT_PROVIDER: "contact",
    GOOGLE_PROVIDER: "calendar_event",
    STRIPE_PROVIDER: "payment_intent",
}
