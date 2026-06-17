from datetime import UTC, date, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_provider_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)

    if isinstance(value, int | float):
        numeric_value = float(value)
        if numeric_value > 10_000_000_000:
            numeric_value = numeric_value / 1000
        return datetime.fromtimestamp(numeric_value, tz=UTC)

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("datetime string is empty")
        if stripped.isdigit():
            return parse_provider_datetime(int(stripped))
        if stripped.endswith("Z"):
            stripped = f"{stripped[:-1]}+00:00"
        return ensure_utc(datetime.fromisoformat(stripped))

    raise ValueError(f"unsupported datetime value: {value!r}")


def format_provider_datetime(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def to_unix_seconds(value: datetime) -> int:
    return int(ensure_utc(value).timestamp())


def to_unix_millis(value: datetime) -> int:
    return int(ensure_utc(value).timestamp() * 1000)
