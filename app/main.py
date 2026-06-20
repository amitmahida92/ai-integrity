from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.problem_2 import router as problem_2_router
from app.api.sync import router as sync_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.include_router(health_router)
app.include_router(sync_router)
app.include_router(problem_2_router)
