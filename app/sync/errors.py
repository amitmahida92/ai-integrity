class SyncOrchestrationError(Exception):
    """Base class for controlled sync orchestration failures."""


class ProviderLockConflictError(SyncOrchestrationError):
    def __init__(self, provider: str) -> None:
        super().__init__(f"{provider} is already being synchronized")
        self.provider = provider


class FailureInjectionDisabledError(SyncOrchestrationError):
    """Demo-only failure injection was requested while disabled."""


class DemoInjectedProviderUnavailableError(SyncOrchestrationError):
    """Demo-only controlled provider outage."""
