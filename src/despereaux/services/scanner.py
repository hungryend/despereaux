"""Library scanner. Two entry points:

  - run_once(): one-shot full scan across all configured libraries
  - watch(): per-library filesystem watchers ingesting new/changed files

Both feed the same ingest_file pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from watchfiles import Change, awatch

from despereaux.config import LibraryConfig, get_settings
from despereaux.db import session_scope
from despereaux.repos.book_delete import delete_by_path
from despereaux.services.ingest import (
    ingest_directory,
    ingest_file,
    is_supported,
    resolve_library_for_path,
)

log = logging.getLogger(__name__)

# Seconds to wait after the LAST size change before ingesting — guards against
# ingesting a half-written file mid-download.
STABILISE_WAIT = 2.0
STABILISE_POLLS = 3


class Scanner:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_result: dict[str, dict[str, int]] | None = None  # per-library counters
        self._watcher_tasks: list[asyncio.Task] = []
        self._pending: dict[Path, asyncio.Task] = {}

    async def run_once(self, *, library_name: str | None = None) -> dict[str, dict[str, int]]:
        """Run a full scan across every configured library, or just one if `library_name` set."""
        async with self._lock:
            settings = get_settings()
            results: dict[str, dict[str, int]] = {}
            for lib in settings.libraries:
                if library_name is not None and lib.name != library_name:
                    continue
                results[lib.name] = await ingest_directory(lib.path, library=lib.name)
            self._last_result = results
            return results

    @property
    def last_result(self) -> dict[str, dict[str, int]] | None:
        return self._last_result

    async def ingest_paths(self, paths: list[Path]) -> dict[str, int]:
        """Ingest a list of explicit file paths (webhook entry point).

        Library is auto-resolved from the configured library roots.
        """
        counters = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
        for path in paths:
            if not path.is_file() or not is_supported(path):
                counters["skipped"] += 1
                continue
            library = resolve_library_for_path(path)
            try:
                result = await ingest_file(path, library=library)
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
        if self._watcher_tasks:
            return
        settings = get_settings()
        for lib in settings.libraries:
            task = asyncio.create_task(self._watch_loop(lib), name=f"library-watcher:{lib.name}")
            self._watcher_tasks.append(task)

    async def stop_watcher(self) -> None:
        for task in self._watcher_tasks:
            task.cancel()
        for task in self._watcher_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._watcher_tasks = []

    async def _watch_loop(self, lib: LibraryConfig) -> None:
        if not lib.path.exists():
            log.warning(
                "watcher: library '%s' path %s does not exist; watcher disabled for it",
                lib.name,
                lib.path,
            )
            return
        log.info("watcher: starting filesystem watch on library '%s' at %s", lib.name, lib.path)
        try:
            async for changes in awatch(lib.path, recursive=True, step=500, debounce=1500):
                for change_type, raw_path in changes:
                    path = Path(raw_path)
                    if change_type == Change.deleted:
                        await self._handle_delete(path)
                        continue
                    if not is_supported(path):
                        continue
                    self._schedule_ingest(path, lib.name)
        except asyncio.CancelledError:
            log.info("watcher for '%s' cancelled", lib.name)
            raise
        except Exception as e:
            log.exception("watcher for '%s' crashed: %s", lib.name, e)

    def _schedule_ingest(self, path: Path, library_name: str) -> None:
        existing = self._pending.pop(path, None)
        if existing is not None:
            existing.cancel()
        task = asyncio.create_task(
            self._ingest_when_stable(path, library_name),
            name=f"ingest:{library_name}:{path.name}",
        )
        self._pending[path] = task

    async def _ingest_when_stable(self, path: Path, library_name: str) -> None:
        try:
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
            log.info("watcher [%s]: ingesting %s", library_name, path)
            try:
                result = await ingest_file(path, library=library_name)
                if result:
                    log.info("watcher [%s]: %s — %s", library_name, path.name, result[1])
            except Exception as e:
                log.exception("watcher [%s]: ingest failed for %s: %s", library_name, path, e)
        finally:
            self._pending.pop(path, None)

    async def _handle_delete(self, path: Path) -> None:
        if not is_supported(path):
            return
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        async with session_scope() as session:
            removed = await delete_by_path(session, str(resolved))
        if removed:
            log.info("watcher: removed %s from library", path.name)


scanner = Scanner()
