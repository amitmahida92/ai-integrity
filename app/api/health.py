from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session

router = APIRouter(tags=["health"])
SessionDep = Annotated[Session, Depends(get_session)]
REQUIRED_TABLES = (
    "normalized_records",
    "sync_checkpoints",
    "sync_runs",
    "sync_source_results",
    "normalized_financial_records",
    "revenue_status_allowlist",
)


def check_database(session: Session) -> None:
    try:
        session.execute(text("select 1")).scalar_one()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "database": "unreachable"},
        ) from exc


def check_required_tables(session: Session) -> None:
    missing_tables: list[str] = []
    try:
        for table_name in REQUIRED_TABLES:
            exists = session.execute(
                text("select to_regclass(:table_name)"),
                {"table_name": f"public.{table_name}"},
            ).scalar_one()
            if exists is None:
                missing_tables.append(table_name)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "database": "unreachable"},
        ) from exc

    if missing_tables:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "database": "not_ready",
                "missing_tables": missing_tables,
            },
        )


@router.get("/health")
def health(session: SessionDep) -> dict[str, str]:
    check_database(session)
    return {"status": "ok", "database": "reachable"}


@router.get("/healthz")
def healthz(session: SessionDep) -> dict[str, str]:
    return health(session)


@router.get("/ready")
def ready(session: SessionDep) -> dict[str, str]:
    check_database(session)
    check_required_tables(session)
    return {"status": "ready", "database": "reachable", "tables": "ready"}
