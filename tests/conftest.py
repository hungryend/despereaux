from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Set env vars BEFORE any despereaux modules are imported by tests,
# so module-level Settings() construction succeeds on every host (incl. Windows dev).
_tmp = Path(tempfile.mkdtemp(prefix="despereaux-test-"))
(_tmp / "ebooks").mkdir(parents=True, exist_ok=True)
(_tmp / "config").mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DESPEREAUX_LIBRARY_PATH", str(_tmp / "ebooks"))
os.environ.setdefault("DESPEREAUX_DATA_DIR", str(_tmp / "config"))
os.environ.setdefault(
    "DESPEREAUX_DB_URL",
    f"sqlite+aiosqlite:///{(_tmp / 'config' / 'despereaux.db').as_posix()}",
)
os.environ.setdefault("DESPEREAUX_DEV_MODE", "true")
os.environ.setdefault("DESPEREAUX_LOG_LEVEL", "WARNING")
os.environ.setdefault("DESPEREAUX_WEBHOOK_TOKEN", "test-token-aaaaaaaaaaaaaaaaaaaaa")


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> None:
    """Create the DB schema once per test session. TestClient doesn't run the lifespan
    by default, so alembic migrations don't fire — bootstrap the tables directly from
    SQLAlchemy metadata."""
    from sqlalchemy import create_engine

    from despereaux.config import get_settings
    from despereaux.models import Base

    settings = get_settings()
    sync_url = settings.db_url.replace("sqlite+aiosqlite", "sqlite")
    engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    engine.dispose()
