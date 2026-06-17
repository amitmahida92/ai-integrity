from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.models import SyncRun, SyncSourceResult


class SyncRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(self, requested_mode: str, requested_sources: list[str]) -> SyncRun:
        sync_run = SyncRun(
            requested_mode=requested_mode,
            requested_sources=requested_sources,
            status="running",
        )
        self.session.add(sync_run)
        self.session.flush()
        return sync_run

    def create_source_result(
        self,
        *,
        sync_run_id: UUID,
        provider: str,
        requested_mode: str,
        effective_mode: str,
        checkpoint_before: dict[str, Any] | None = None,
    ) -> SyncSourceResult:
        result = SyncSourceResult(
            sync_run_id=sync_run_id,
            provider=provider,
            requested_mode=requested_mode,
            effective_mode=effective_mode,
            status="running",
            checkpoint_before=checkpoint_before,
        )
        self.session.add(result)
        self.session.flush()
        return result

    def finish_source_result(
        self,
        result: SyncSourceResult,
        *,
        status: str,
        records_fetched: int = 0,
        records_upserted: int = 0,
        records_rejected: int = 0,
        pages_fetched: int = 0,
        checkpoint_after: dict[str, Any] | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> SyncSourceResult:
        result.status = status
        result.records_fetched = records_fetched
        result.records_upserted = records_upserted
        result.records_rejected = records_rejected
        result.pages_fetched = pages_fetched
        result.checkpoint_after = checkpoint_after
        result.error_type = error_type
        result.error_message = error_message
        result.finished_at = datetime.now(UTC)
        self.session.flush()
        return result

    def finish_run(self, sync_run: SyncRun, source_statuses: list[str]) -> SyncRun:
        sync_run.status = derive_run_status(source_statuses)
        sync_run.finished_at = datetime.now(UTC)
        self.session.flush()
        return sync_run

    def get_run(self, run_id: UUID) -> SyncRun | None:
        statement = (
            select(SyncRun)
            .options(selectinload(SyncRun.source_results))
            .where(SyncRun.id == run_id)
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_runs(self, limit: int = 20) -> list[SyncRun]:
        bounded_limit = max(1, min(limit, 100))
        statement = (
            select(SyncRun)
            .options(selectinload(SyncRun.source_results))
            .order_by(desc(SyncRun.started_at))
            .limit(bounded_limit)
        )
        return list(self.session.execute(statement).scalars())


def derive_run_status(source_statuses: list[str]) -> str:
    if source_statuses and all(status == "succeeded" for status in source_statuses):
        return "succeeded"
    if source_statuses and all(status == "failed" for status in source_statuses):
        return "failed"
    return "completed_with_errors"
