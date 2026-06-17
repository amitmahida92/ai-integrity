import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import NormalizedRecord


@dataclass(frozen=True)
class NormalizedRecordInput:
    provider: str
    entity_type: str
    external_id: str
    canonical_data: dict[str, Any]
    raw_payload: dict[str, Any]
    source_updated_at: datetime | None = None


def payload_hash(raw_payload: dict[str, Any]) -> str:
    serialized = json.dumps(raw_payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class NormalizedRecordRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, provider: str, entity_type: str, external_id: str) -> NormalizedRecord | None:
        statement = select(NormalizedRecord).where(
            NormalizedRecord.provider == provider,
            NormalizedRecord.entity_type == entity_type,
            NormalizedRecord.external_id == external_id,
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_by_entity(self, entity_type: str, limit: int = 50) -> list[NormalizedRecord]:
        bounded_limit = max(1, min(limit, 100))
        statement = (
            select(NormalizedRecord)
            .where(NormalizedRecord.entity_type == entity_type)
            .order_by(
                desc(NormalizedRecord.source_updated_at).nullslast(),
                desc(NormalizedRecord.last_seen_at),
            )
            .limit(bounded_limit)
        )
        return list(self.session.execute(statement).scalars())

    def upsert(self, record: NormalizedRecordInput) -> NormalizedRecord:
        seen_at = datetime.now(UTC)
        statement = insert(NormalizedRecord).values(
            provider=record.provider,
            entity_type=record.entity_type,
            external_id=record.external_id,
            source_updated_at=record.source_updated_at,
            canonical_data=record.canonical_data,
            raw_payload=record.raw_payload,
            payload_hash=payload_hash(record.raw_payload),
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )

        excluded = statement.excluded
        accepts_provider_version = or_(
            NormalizedRecord.source_updated_at.is_(None),
            and_(
                excluded.source_updated_at.is_not(None),
                excluded.source_updated_at >= NormalizedRecord.source_updated_at,
            ),
        )

        statement = statement.on_conflict_do_update(
            index_elements=[
                NormalizedRecord.provider,
                NormalizedRecord.entity_type,
                NormalizedRecord.external_id,
            ],
            set_={
                "source_updated_at": case(
                    (accepts_provider_version, excluded.source_updated_at),
                    else_=NormalizedRecord.source_updated_at,
                ),
                "canonical_data": case(
                    (accepts_provider_version, excluded.canonical_data),
                    else_=NormalizedRecord.canonical_data,
                ),
                "raw_payload": case(
                    (accepts_provider_version, excluded.raw_payload),
                    else_=NormalizedRecord.raw_payload,
                ),
                "payload_hash": case(
                    (accepts_provider_version, excluded.payload_hash),
                    else_=NormalizedRecord.payload_hash,
                ),
                "last_seen_at": excluded.last_seen_at,
            },
        ).returning(NormalizedRecord)

        return self.session.execute(statement).scalar_one()

    def upsert_many(self, records: list[NormalizedRecordInput]) -> int:
        for record in records:
            self.upsert(record)
        return len(records)

    def count(self) -> int:
        statement = select(func.count()).select_from(NormalizedRecord)
        return self.session.execute(statement).scalar_one()
