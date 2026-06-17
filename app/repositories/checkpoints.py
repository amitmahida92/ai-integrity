from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import SyncCheckpoint


class SyncCheckpointRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, provider: str) -> SyncCheckpoint | None:
        statement = select(SyncCheckpoint).where(SyncCheckpoint.provider == provider)
        return self.session.execute(statement).scalar_one_or_none()

    def upsert(self, provider: str, checkpoint_data: dict[str, Any]) -> SyncCheckpoint:
        statement = insert(SyncCheckpoint).values(
            provider=provider,
            checkpoint_data=checkpoint_data,
        )

        statement = statement.on_conflict_do_update(
            index_elements=[SyncCheckpoint.provider],
            set_={
                "checkpoint_data": statement.excluded.checkpoint_data,
                "updated_at": func.now(),
            },
        ).returning(SyncCheckpoint)

        return self.session.execute(statement).scalar_one()
