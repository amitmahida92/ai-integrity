import time
from collections.abc import Callable
from dataclasses import dataclass

from app.sources.exceptions import (
    ProviderClientError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderTransientError,
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0


def call_with_retry[T](
    operation: Callable[[], T],
    *,
    retry_policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return operation()
        except (ProviderRateLimitError, ProviderTimeoutError, ProviderTransientError) as exc:
            if attempt >= retry_policy.max_attempts:
                raise
            retry_after = (
                exc.retry_after_seconds if isinstance(exc, ProviderRateLimitError) else None
            )
            delay = retry_after or retry_policy.base_delay_seconds * (2 ** (attempt - 1))
            sleep(min(delay, retry_policy.max_delay_seconds))
        except ProviderClientError:
            raise
        except Exception as exc:
            raise ProviderClientError("provider client raised an unexpected exception") from exc
