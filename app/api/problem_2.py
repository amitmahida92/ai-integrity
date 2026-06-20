from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.sync import require_admin_api_key
from app.core.config import get_settings
from app.db.session import get_session
from app.services.revenue_metrics import RevenueMetricsService
from app.sources.exceptions import ProviderClientError, ProviderResponseError
from app.sources.stripe import StripeHttpClient

router = APIRouter(
    prefix="/api/v1",
    tags=["problem-2"],
    dependencies=[Depends(require_admin_api_key)],
)

SessionDep = Annotated[Session, Depends(get_session)]


def get_revenue_metrics_service(session: SessionDep) -> RevenueMetricsService:
    return RevenueMetricsService(session)


RevenueMetricsServiceDep = Annotated[RevenueMetricsService, Depends(get_revenue_metrics_service)]


class Problem2SeedResponse(BaseModel):
    status: str
    allowlist_rows: int
    financial_records: int


class StripeImportResponse(BaseModel):
    status: str
    imported_records: int
    rejected_records: int
    pages_fetched: int


class RevenueSummaryResponse(BaseModel):
    from_date: date
    to_date: date
    totals_by_currency: dict[str, int]
    metric_name: str = "collected_revenue"
    metric_version: str = "v1_allowlist"


class RevenueBucketResponse(BaseModel):
    date: date
    totals_by_currency: dict[str, int]


class RevenueBreakdownResponse(BaseModel):
    from_date: date
    to_date: date
    grain: str
    buckets: list[RevenueBucketResponse]
    aggregate_totals_by_currency: dict[str, int]


@router.post("/problem-2/seed", response_model=Problem2SeedResponse)
def seed_problem_2(service: RevenueMetricsServiceDep) -> Problem2SeedResponse:
    result = service.seed_demo_data()
    return Problem2SeedResponse(status="seeded", **result)


@router.post("/problem-2/import-stripe", response_model=StripeImportResponse)
def import_stripe_payment_intents(service: RevenueMetricsServiceDep) -> StripeImportResponse:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe credentials are not configured",
        )

    try:
        result = service.import_stripe_payment_intents(
            StripeHttpClient(secret_key=settings.stripe_secret_key)
        )
    except (ProviderClientError, ProviderResponseError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe import failed",
        ) from exc

    return StripeImportResponse(status="imported", **result)


@router.get("/metrics/revenue/summary", response_model=RevenueSummaryResponse)
def revenue_summary(
    service: RevenueMetricsServiceDep,
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
) -> RevenueSummaryResponse:
    _validate_date_range(from_date, to_date)
    return RevenueSummaryResponse(
        from_date=from_date,
        to_date=to_date,
        totals_by_currency=service.collected_revenue_summary(
            from_date=from_date,
            to_date=to_date,
        ),
    )


@router.get("/metrics/revenue/breakdown", response_model=RevenueBreakdownResponse)
def revenue_breakdown(
    service: RevenueMetricsServiceDep,
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
    grain: Annotated[str, Query(pattern="^day$")] = "day",
) -> RevenueBreakdownResponse:
    _validate_date_range(from_date, to_date)
    buckets, aggregate_totals = service.collected_revenue_breakdown(
        from_date=from_date,
        to_date=to_date,
    )
    return RevenueBreakdownResponse(
        from_date=from_date,
        to_date=to_date,
        grain=grain,
        buckets=buckets,
        aggregate_totals_by_currency=aggregate_totals,
    )


def _validate_date_range(from_date: date, to_date: date) -> None:
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="to_date must be on or after from_date",
        )
