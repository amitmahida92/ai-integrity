from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session

router = APIRouter(tags=["health"])
SessionDep = Annotated[Session, Depends(get_session)]


def check_database(session: Session) -> None:
    try:
        session.execute(text("select 1")).scalar_one()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "database": "unreachable"},
        ) from exc


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
    return {"status": "ready", "database": "reachable"}
