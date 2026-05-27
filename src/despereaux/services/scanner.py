"""Library scanner. Phase 1 = one-shot scan; Phase 2 adds watchfiles live indexing."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from despereaux.config import get_settings
from despereaux.services.ingest import ingest_directory

log = logging.getLogger(__name__)


class Scanner:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_result: dict[str, int] | None = None

    async def run_once(self, root: Path | None = None) -> dict[str, int]:
        async with self._lock:
            settings = get_settings()
            base = root or settings.library_path
            log.info("scanning library at %s", base)
            self._last_result = await ingest_directory(base)
            return self._last_result

    @property
    def last_result(self) -> dict[str, int] | None:
        return self._last_result


scanner = Scanner()
