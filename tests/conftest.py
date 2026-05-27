from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
