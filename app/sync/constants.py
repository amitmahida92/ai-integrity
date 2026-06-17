from enum import StrEnum


class ProviderName(StrEnum):
    HUBSPOT = "hubspot"
    GOOGLE_CALENDAR = "google_calendar"
    STRIPE = "stripe"


class SyncMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


SUPPORTED_PROVIDERS: tuple[ProviderName, ...] = (
    ProviderName.HUBSPOT,
    ProviderName.GOOGLE_CALENDAR,
    ProviderName.STRIPE,
)

SUPPORTED_PROVIDER_VALUES = tuple(provider.value for provider in SUPPORTED_PROVIDERS)
