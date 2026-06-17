from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import SyncRun, SyncSourceResult
from app.repositories.checkpoints import SyncCheckpointRepository
from app.repositories.normalized_records import NormalizedRecordRepository
from app.repositories.sync_runs import SyncRunRepository
from app.sources.types import FetchResult
from app.sync.constants import ProviderName, SyncMode
from app.sync.errors import (
    DemoInjectedProviderUnavailableError,
    FailureInjectionDisabledError,
    ProviderLockConflictError,
)
from app.sync.providers import ProviderAdapterFactory

SessionFactory = Callable[[], Session]

LOCK_NAMESPACE = 20_260_618
SENSITIVE_WORDS = (
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "bearer",
    "token",
    "sk_",
)


@dataclass(frozen=True)
class DemoFailureOptions:
    provider_unavailable: str | None = None
    malformed_records_provider: str | None = None
    google_expired_sync_token: bool = False

    @property
    def requested(self) -> bool:
        return bool(
            self.provider_unavailable
            or self.malformed_records_provider
            or self.google_expired_sync_token
        )


@dataclass(frozen=True)
class SafeErrorSummary:
    error_type: str
    message: str


class ProviderAdvisoryLocks:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._providers: list[str] = []

    def acquire(self, providers: list[str]) -> None:
        self._session = self._session_factory()
        try:
            for provider in providers:
                acquired = self._session.execute(
                    text("select pg_try_advisory_lock(:namespace, hashtext(:provider))"),
                    {"namespace": LOCK_NAMESPACE, "provider": provider},
                ).scalar_one()
                if not acquired:
                    self.release()
                    raise ProviderLockConflictError(provider)
                self._providers.append(provider)
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        if self._session is None:
            return
        try:
            for provider in reversed(self._providers):
                self._session.execute(
                    text("select pg_advisory_unlock(:namespace, hashtext(:provider))"),
                    {"namespace": LOCK_NAMESPACE, "provider": provider},
                )
        finally:
            self._providers.clear()
            self._session.close()
            self._session = None


class SyncOrchestrator:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        adapter_factory: ProviderAdapterFactory,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._adapter_factory = adapter_factory
        self._settings = settings

    def run_sync(
        self,
        *,
        requested_mode: str,
        providers: list[str],
        demo_options: DemoFailureOptions | None = None,
    ) -> SyncRun:
        demo_options = demo_options or DemoFailureOptions()
        self._validate_demo_options(demo_options)

        locks = ProviderAdvisoryLocks(self._session_factory)
        locks.acquire(providers)
        try:
            run_id = self._create_run(requested_mode=requested_mode, providers=providers)
            source_statuses = [
                self._execute_provider(
                    run_id=run_id,
                    provider=provider,
                    requested_mode=requested_mode,
                    demo_options=demo_options,
                )
                for provider in providers
            ]
            return self._finish_run(run_id=run_id, source_statuses=source_statuses)
        finally:
            locks.release()

    def get_run(self, run_id: UUID) -> SyncRun | None:
        with self._session_factory() as session:
            return SyncRunRepository(session).get_run(run_id)

    def list_runs(self, limit: int = 20) -> list[SyncRun]:
        with self._session_factory() as session:
            return SyncRunRepository(session).list_runs(limit=limit)

    def _validate_demo_options(self, demo_options: DemoFailureOptions) -> None:
        enabled = (
            self._settings.demo_failure_injection_enabled
            or self._settings.debug_sync_tools_enabled
        )
        if demo_options.requested and not enabled:
            raise FailureInjectionDisabledError(
                "Demo failure injection is disabled for this environment"
            )

    def _create_run(self, *, requested_mode: str, providers: list[str]) -> UUID:
        with self._session_factory() as session:
            sync_run = SyncRunRepository(session).create_run(
                requested_mode=requested_mode,
                requested_sources=providers,
            )
            session.commit()
            return sync_run.id

    def _finish_run(self, *, run_id: UUID, source_statuses: list[str]) -> SyncRun:
        with self._session_factory() as session:
            repository = SyncRunRepository(session)
            sync_run = repository.get_run(run_id)
            if sync_run is None:
                raise RuntimeError("sync run disappeared before completion")
            repository.finish_run(sync_run, source_statuses)
            session.commit()
            loaded_run = repository.get_run(run_id)
            if loaded_run is None:
                raise RuntimeError("sync run disappeared after completion")
            return loaded_run

    def _execute_provider(
        self,
        *,
        run_id: UUID,
        provider: str,
        requested_mode: str,
        demo_options: DemoFailureOptions,
    ) -> str:
        result_id, checkpoint_before = self._create_source_result(
            run_id=run_id,
            provider=provider,
            requested_mode=requested_mode,
        )
        effective_mode = requested_mode
        records_fetched = 0
        records_rejected = 0
        pages_fetched = 0

        try:
            fetch_result = self._fetch_provider(
                provider=provider,
                requested_mode=requested_mode,
                checkpoint_before=checkpoint_before,
                demo_options=demo_options,
            )
            if demo_options.malformed_records_provider == provider:
                fetch_result = replace(
                    fetch_result,
                    records_fetched=fetch_result.records_fetched + 1,
                    rejected_records=fetch_result.rejected_records + 1,
                )

            effective_mode = fetch_result.effective_mode
            records_fetched = fetch_result.records_fetched
            records_rejected = fetch_result.rejected_records
            pages_fetched = fetch_result.pages_fetched

            records_upserted = self._persist_provider_success(
                result_id=result_id,
                provider=provider,
                requested_mode=requested_mode,
                fetch_result=fetch_result,
            )
            return self._source_status_after_success(
                result_id=result_id,
                records_upserted=records_upserted,
            )
        except Exception as exc:
            self._mark_source_failed(
                result_id=result_id,
                effective_mode=effective_mode,
                records_fetched=records_fetched,
                records_rejected=records_rejected,
                pages_fetched=pages_fetched,
                error=safe_error_summary(exc),
            )
            return "failed"

    def _create_source_result(
        self,
        *,
        run_id: UUID,
        provider: str,
        requested_mode: str,
    ) -> tuple[UUID, dict[str, Any]]:
        with self._session_factory() as session:
            checkpoint = SyncCheckpointRepository(session).get(provider)
            checkpoint_before = dict(checkpoint.checkpoint_data) if checkpoint is not None else {}
            result = SyncRunRepository(session).create_source_result(
                sync_run_id=run_id,
                provider=provider,
                requested_mode=requested_mode,
                effective_mode=requested_mode,
                checkpoint_before=checkpoint_before,
            )
            session.commit()
            return result.id, checkpoint_before

    def _fetch_provider(
        self,
        *,
        provider: str,
        requested_mode: str,
        checkpoint_before: dict[str, Any],
        demo_options: DemoFailureOptions,
    ) -> FetchResult:
        if demo_options.provider_unavailable == provider:
            raise DemoInjectedProviderUnavailableError(
                f"Demo-only provider unavailable injection for {provider}"
            )

        adapter = self._adapter_factory.create(provider)
        checkpoint_for_fetch = checkpoint_before
        if (
            demo_options.google_expired_sync_token
            and provider == ProviderName.GOOGLE_CALENDAR.value
            and requested_mode == SyncMode.INCREMENTAL.value
        ):
            checkpoint_for_fetch = {
                **checkpoint_before,
                "sync_token": "demo-expired-sync-token",
            }

        if requested_mode == SyncMode.FULL.value:
            return adapter.fetch_full(checkpoint_before)
        return adapter.fetch_incremental(checkpoint_for_fetch)

    def _persist_provider_success(
        self,
        *,
        result_id: UUID,
        provider: str,
        requested_mode: str,
        fetch_result: FetchResult,
    ) -> int:
        with self._session_factory() as session:
            try:
                records_upserted = NormalizedRecordRepository(session).upsert_many(
                    fetch_result.records
                )
                SyncCheckpointRepository(session).upsert(
                    provider,
                    fetch_result.next_checkpoint_data,
                )
                result = _require_source_result(session, result_id)
                SyncRunRepository(session).finish_source_result(
                    result,
                    status="succeeded",
                    records_fetched=fetch_result.records_fetched,
                    records_upserted=records_upserted,
                    records_rejected=fetch_result.rejected_records,
                    pages_fetched=fetch_result.pages_fetched,
                    checkpoint_after=fetch_result.next_checkpoint_data,
                )
                result.requested_mode = requested_mode
                result.effective_mode = fetch_result.effective_mode
                session.commit()
                return records_upserted
            except Exception:
                session.rollback()
                raise

    def _source_status_after_success(self, *, result_id: UUID, records_upserted: int) -> str:
        if records_upserted < 0:
            raise RuntimeError("records_upserted cannot be negative")
        return "succeeded"

    def _mark_source_failed(
        self,
        *,
        result_id: UUID,
        effective_mode: str,
        records_fetched: int,
        records_rejected: int,
        pages_fetched: int,
        error: SafeErrorSummary,
    ) -> None:
        with self._session_factory() as session:
            result = _require_source_result(session, result_id)
            result.effective_mode = effective_mode
            SyncRunRepository(session).finish_source_result(
                result,
                status="failed",
                records_fetched=records_fetched,
                records_upserted=0,
                records_rejected=records_rejected,
                pages_fetched=pages_fetched,
                checkpoint_after=None,
                error_type=error.error_type,
                error_message=error.message,
            )
            session.commit()


def _require_source_result(session: Session, result_id: UUID) -> SyncSourceResult:
    result = session.get(SyncSourceResult, result_id)
    if result is None:
        raise RuntimeError("sync source result disappeared before completion")
    return result


def safe_error_summary(exc: Exception) -> SafeErrorSummary:
    error_type = exc.__class__.__name__
    if isinstance(exc, SQLAlchemyError):
        return SafeErrorSummary(error_type=error_type, message="database persistence failed")

    message = str(exc) or "source execution failed"
    lowered = message.lower()
    if any(word in lowered for word in SENSITIVE_WORDS):
        message = "source execution failed"
    if len(message) > 240:
        message = f"{message[:237]}..."
    return SafeErrorSummary(error_type=error_type, message=message)
