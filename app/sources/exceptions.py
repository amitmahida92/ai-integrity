class SourceAdapterError(Exception):
    """Base class for provider adapter failures."""


class ProviderClientError(SourceAdapterError):
    """Provider client failed in a controlled, source-scoped way."""


class ProviderRateLimitError(ProviderClientError):
    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderTransientError(ProviderClientError):
    """Provider returned a retryable transient error."""


class ProviderTimeoutError(ProviderTransientError):
    """Provider request timed out."""


class ProviderResponseError(ProviderClientError):
    """Provider returned a non-retryable response or malformed response body."""


class GoogleSyncTokenExpired(ProviderClientError):
    """Google Calendar returned HTTP 410 for an incremental sync token."""


class RecordRejectedError(SourceAdapterError):
    """One provider record is malformed and should be skipped, not crash the page."""
