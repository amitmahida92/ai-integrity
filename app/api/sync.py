from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models import NormalizedRecord, SyncRun, SyncSourceResult
from app.repositories.normalized_records import NormalizedRecordRepository
from app.sync.constants import SUPPORTED_PROVIDERS, ProviderName, SyncMode
from app.sync.dependencies import get_sync_orchestrator
from app.sync.errors import FailureInjectionDisabledError, ProviderLockConflictError
from app.sync.orchestrator import DemoFailureOptions, SyncOrchestrator

router = APIRouter(prefix="/api/v1", tags=["sync"])

SessionDep = Annotated[Session, Depends(get_session)]
SyncOrchestratorDep = Annotated[SyncOrchestrator, Depends(get_sync_orchestrator)]

RUN_STATUS_RESPONSE = {
    "running": "running",
    "succeeded": "success",
    "completed_with_errors": "partial_success",
    "failed": "failed",
}

SOURCE_STATUS_RESPONSE = {
    "running": "running",
    "succeeded": "success",
    "failed": "failed",
}


class SyncDebugRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_unavailable: ProviderName | None = Field(
        default=None,
        validation_alias=AliasChoices("provider_unavailable", "fail_provider", "fail_source"),
    )
    malformed_records_provider: ProviderName | None = Field(
        default=None,
        validation_alias=AliasChoices("malformed_records_provider", "malformed_provider"),
    )
    google_expired_sync_token: bool = Field(
        default=False,
        validation_alias=AliasChoices("google_expired_sync_token", "stale_google_cursor"),
    )

    def to_options(self) -> DemoFailureOptions:
        return DemoFailureOptions(
            provider_unavailable=(
                self.provider_unavailable.value if self.provider_unavailable else None
            ),
            malformed_records_provider=(
                self.malformed_records_provider.value
                if self.malformed_records_provider
                else None
            ),
            google_expired_sync_token=self.google_expired_sync_token,
        )


class SyncRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: SyncMode
    providers: list[ProviderName] | None = Field(
        default=None,
        validation_alias=AliasChoices("providers", "sources"),
    )
    debug: SyncDebugRequest | None = None

    @field_validator("providers")
    @classmethod
    def validate_providers(
        cls,
        providers: list[ProviderName] | None,
    ) -> list[ProviderName] | None:
        if providers is None:
            return providers
        if not providers:
            raise ValueError("providers must contain at least one provider")
        provider_values = [provider.value for provider in providers]
        if len(provider_values) != len(set(provider_values)):
            raise ValueError("providers must not contain duplicates")
        return providers

    def provider_values(self) -> list[str]:
        if self.providers is None:
            return [provider.value for provider in SUPPORTED_PROVIDERS]
        return [provider.value for provider in self.providers]

    def demo_options(self) -> DemoFailureOptions:
        return self.debug.to_options() if self.debug else DemoFailureOptions()


class SingleProviderSyncRequest(BaseModel):
    mode: SyncMode
    debug: SyncDebugRequest | None = None

    def demo_options(self) -> DemoFailureOptions:
        return self.debug.to_options() if self.debug else DemoFailureOptions()


class ProviderSyncResultResponse(BaseModel):
    provider: str
    requested_mode: str
    effective_mode: str
    status: str
    fetched_count: int
    upserted_count: int
    rejected_count: int
    pages_fetched: int
    fallback_full_sync: bool
    started_at: datetime
    completed_at: datetime | None
    error_type: str | None = None
    error_summary: str | None = None


class SyncRunResponse(BaseModel):
    id: UUID
    requested_mode: str
    requested_providers: list[str]
    status: str
    started_at: datetime
    completed_at: datetime | None
    provider_results: list[ProviderSyncResultResponse]


class SyncRunListResponse(BaseModel):
    runs: list[SyncRunResponse]


class NormalizedRecordResponse(BaseModel):
    id: UUID
    provider: str
    entity_type: str
    external_id: str
    source_updated_at: datetime | None
    canonical_data: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime


class RecordsResponse(BaseModel):
    records: list[NormalizedRecordResponse]


class RecordCountResponse(BaseModel):
    provider: str
    entity_type: str
    count: int


class RecordCountsResponse(BaseModel):
    total: int
    counts: list[RecordCountResponse]


@router.post("/sync", response_model=SyncRunResponse)
def trigger_sync(request: SyncRequest, orchestrator: SyncOrchestratorDep) -> SyncRunResponse:
    sync_run = _run_sync(
        orchestrator=orchestrator,
        mode=request.mode.value,
        providers=request.provider_values(),
        demo_options=request.demo_options(),
    )
    return serialize_sync_run(sync_run)


@router.post("/sync/{provider}", response_model=SyncRunResponse)
def trigger_provider_sync(
    provider: ProviderName,
    request: SingleProviderSyncRequest,
    orchestrator: SyncOrchestratorDep,
) -> SyncRunResponse:
    sync_run = _run_sync(
        orchestrator=orchestrator,
        mode=request.mode.value,
        providers=[provider.value],
        demo_options=request.demo_options(),
    )
    return serialize_sync_run(sync_run)


@router.get("/sync-runs", response_model=SyncRunListResponse)
def list_sync_runs(
    orchestrator: SyncOrchestratorDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SyncRunListResponse:
    return SyncRunListResponse(
        runs=[serialize_sync_run(sync_run) for sync_run in orchestrator.list_runs(limit=limit)]
    )


@router.get("/sync-runs/{run_id}", response_model=SyncRunResponse)
def get_sync_run(run_id: UUID, orchestrator: SyncOrchestratorDep) -> SyncRunResponse:
    sync_run = orchestrator.get_run(run_id)
    if sync_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sync run not found")
    return serialize_sync_run(sync_run)


@router.get("/records", response_model=RecordsResponse)
def list_records(
    session: SessionDep,
    provider: ProviderName | None = None,
    entity_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecordsResponse:
    provider_value = provider.value if provider else None
    records = NormalizedRecordRepository(session).list_records(
        provider=provider_value,
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )
    return RecordsResponse(records=[serialize_record(record) for record in records])


@router.get("/records/counts", response_model=RecordCountsResponse)
def get_record_counts(session: SessionDep) -> RecordCountsResponse:
    repository = NormalizedRecordRepository(session)
    counts = [
        RecordCountResponse(provider=provider, entity_type=entity_type, count=count)
        for provider, entity_type, count in repository.count_groups()
    ]
    return RecordCountsResponse(total=repository.count(), counts=counts)


def _run_sync(
    *,
    orchestrator: SyncOrchestrator,
    mode: str,
    providers: list[str],
    demo_options: DemoFailureOptions,
) -> SyncRun:
    try:
        return orchestrator.run_sync(
            requested_mode=mode,
            providers=providers,
            demo_options=demo_options,
        )
    except FailureInjectionDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except ProviderLockConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def serialize_sync_run(sync_run: SyncRun) -> SyncRunResponse:
    source_results = sorted(sync_run.source_results, key=lambda result: result.started_at)
    return SyncRunResponse(
        id=sync_run.id,
        requested_mode=sync_run.requested_mode,
        requested_providers=list(sync_run.requested_sources),
        status=RUN_STATUS_RESPONSE.get(sync_run.status, sync_run.status),
        started_at=sync_run.started_at,
        completed_at=sync_run.finished_at,
        provider_results=[
            serialize_source_result(source_result) for source_result in source_results
        ],
    )


def serialize_source_result(source_result: SyncSourceResult) -> ProviderSyncResultResponse:
    return ProviderSyncResultResponse(
        provider=source_result.provider,
        requested_mode=source_result.requested_mode,
        effective_mode=source_result.effective_mode,
        status=SOURCE_STATUS_RESPONSE.get(source_result.status, source_result.status),
        fetched_count=source_result.records_fetched,
        upserted_count=source_result.records_upserted,
        rejected_count=source_result.records_rejected,
        pages_fetched=source_result.pages_fetched,
        fallback_full_sync=source_result.effective_mode == "recovery_full",
        started_at=source_result.started_at,
        completed_at=source_result.finished_at,
        error_type=source_result.error_type,
        error_summary=source_result.error_message,
    )


def serialize_record(record: NormalizedRecord) -> NormalizedRecordResponse:
    return NormalizedRecordResponse(
        id=record.id,
        provider=record.provider,
        entity_type=record.entity_type,
        external_id=record.external_id,
        source_updated_at=record.source_updated_at,
        canonical_data=record.canonical_data,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
    )
