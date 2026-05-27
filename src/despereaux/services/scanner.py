"""Library scanner. Two entry points:

  - run_once(): one-shot full scan, used at boot and via /api/admin/scan
  - watch(): long-running filesystem watcher that ingests new/changed files in real time

Both feed the same ingest_file pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from watchfiles import Change, awatch

from despereaux.config import get_settings
from despereaux.db import session_scope
from despereaux.repos.book_delete import delete_by_path
from despereaux.services.ingest import ingest_directory, ingest_file, is_supported

log = logging.getLogger(__name__)

# Seconds to wait after the LAST size change before ingesting — guards against
# ingesting a half-written file mid-download.
STABILISE_WAIT = 2.0
STABILISE_POLLS = 3


class Scanner:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_result: dict[str, int] | None = None
        self._watcher_task: asyncio.Task | None = None
        self._pending: dict[Path, asyncio.Task] = {}

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

    async def ingest_paths(self, paths: list[Path]) -> dict[str, int]:
        """Ingest a list of explicit file paths. Used by the /sync webhook."""
        counters = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
        for path in paths:
            if not path.is_file() or not is_supported(path):
                counters["skipped"] += 1
                continue
            try:
                result = await ingest_file(path)
            except Exception as e:
                log.exception("ingest failed for %s: %s", path, e)
                counters["failed"] += 1
                continue
            if result is None:
                counters["skipped"] += 1
            else:
                counters[result[1]] += 1
        log.info("targeted ingest: %s", counters)
        return counters

    def start_watcher(self) -> None:
        if self._watcher_task is not None:
            return
        self._watcher_task = asyncio.create_task(self._watch_loop(), name="library-watcher")

    async def stop_watcher(self) -> None:
        if self._watcher_task is None:
            return
        self._watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._watcher_task
        self._watcher_task = None

    async def _watch_loop(self) -> None:
        settings = get_settings()
        base = settings.library_path
        if not base.exists():
            log.warning("watcher: library path %s does not exist; watcher disabled", base)
            return
        log.info("watcher: starting filesystem watch on %s", base)
        try:
            async for changes in awatch(base, recursive=True, step=500, debounce=1500):
                for change_type, raw_path in changes:
                    path = Path(raw_path)
                    if change_type == Change.deleted:
                        await self._handle_delete(path)
                        continue
                    if not is_supported(path):
                        continue
                    self._schedule_ingest(path)
        except asyncio.CancelledError:
            log.info("watcher: cancelled")
            raise
        except Exception as e:
            log.exception("watcher crashed: %s", e)

    def _schedule_ingest(self, path: Path) -> None:
        existing = self._pending.pop(path, None)
        if existing is not None:
            existing.cancel()
        task = asyncio.create_task(self._ingest_when_stable(path), name=f"ingest:{path.name}")
        self._pending[path] = task

    async def _ingest_when_stable(self, path: Path) -> None:
        try:
            # Wait until the file has stopped growing — STABILISE_POLLS consecutive
            # polls at STABILISE_WAIT intervals must return the same size.
            last_size = -1
            stable = 0
            for _ in range(20):  # cap total wait at ~40s
                if not path.exists():
                    return
                try:
                    size = path.stat().st_size
                except OSError:
                    return
                if size == last_size and size > 0:
                    stable += 1
                    if stable >= STABILISE_POLLS:
                        break
                else:
                    stable = 0
                    last_size = size
                await asyncio.sleep(STABILISE_WAIT)

            if not path.exists():
                return
            log.info("watcher: ingesting %s", path)
            try:
                result = await ingest_file(path)
                if result:
                    log.info("watcher: %s — %s", path.name, result[1])
            except Exception as e:
                log.exception("watcher: ingest failed for %s: %s", path, e)
        finally:
            self._pending.pop(path, None)

    async def _handle_delete(self, path: Path) -> None:
        if not is_supported(path):
            return
        # Match how ingest_file stores it (absolute, resolved). The file is gone so
        # Path.resolve() with strict=False still does the platform-native normalisation.
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        async with session_scope() as session:
            removed = await delete_by_path(session, str(resolved))
        if removed:
            log.info("watcher: removed %s from library", path.name)


scanner = Scanner()
