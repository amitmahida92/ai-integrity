from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.repositories.normalized_records import NormalizedRecordInput

EffectiveMode = Literal["full", "incremental", "recovery_full"]


@dataclass(frozen=True)
class ProviderPage:
    items: list[Any]
    next_cursor: str | None = None
    next_sync_token: str | None = None
    has_more: bool = False


@dataclass(frozen=True)
class FetchResult:
    provider: str
    effective_mode: EffectiveMode
    records: list[NormalizedRecordInput]
    records_fetched: int
    rejected_records: int
    pages_fetched: int
    next_checkpoint_data: dict[str, Any]
    started_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
