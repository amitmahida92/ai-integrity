from app.core.config import get_settings
from app.db.session import SessionLocal
from app.sync.orchestrator import SyncOrchestrator
from app.sync.providers import HttpProviderAdapterFactory


def get_sync_orchestrator() -> SyncOrchestrator:
    settings = get_settings()
    return SyncOrchestrator(
        session_factory=SessionLocal,
        adapter_factory=HttpProviderAdapterFactory(settings),
        settings=settings,
    )
