"""Scanner regression tests: multi-library run_once counters, targeted
webhook ingest, delete handling, and the stabilisation wait.

The watchfiles `awatch` loop itself is deliberately NOT tested here — it's
wall-clock debounced and flaky on Windows dev boxes. The container smoke tier
exercises the real watcher by dropping files into /ebooks. Everything the loop
body calls IS covered below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from despereaux.config import LibraryConfig, get_settings
from despereaux.db import session_scope
from despereaux.repos.books import get_book_by_path
from despereaux.services import scanner as scanner_mod
from despereaux.services.scanner import Scanner
from tests.util import write_cbz, write_epub


@pytest.fixture(autouse=True)
def _no_enrichment(monkeypatch):
    async def _noop(session, book):
        return None

    import despereaux.services.metadata_apply as metadata_apply

    monkeypatch.setattr(metadata_apply, "maybe_auto_enrich", _noop)


@pytest.fixture
def two_libraries(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    lib_a = tmp_path / "lib-a"
    lib_b = tmp_path / "lib-b"
    lib_a.mkdir()
    lib_b.mkdir()
    monkeypatch.setattr(
        get_settings(),
        "libraries",
        [LibraryConfig(name="ScanA", path=lib_a), LibraryConfig(name="ScanB", path=lib_b)],
    )
    return lib_a, lib_b


async def test_run_once_scans_all_libraries(two_libraries) -> None:
    lib_a, lib_b = two_libraries
    write_cbz(lib_a / "comic.cbz")
    write_epub(lib_b / "novel.epub", title="Scanner Novel A")
    (lib_a / "junk.txt").write_text("not an ebook")

    s = Scanner()
    results = await s.run_once()
    assert results["ScanA"]["created"] == 1
    assert results["ScanA"]["skipped"] == 1  # junk.txt
    assert results["ScanB"]["created"] == 1
    assert s.last_result == results

    # Second pass: nothing changed.
    results = await s.run_once()
    assert results["ScanA"]["unchanged"] == 1
    assert results["ScanB"]["unchanged"] == 1


async def test_run_once_single_library_filter(two_libraries) -> None:
    lib_a, lib_b = two_libraries
    write_epub(lib_a / "a.epub", title="Scanner Only A")
    write_epub(lib_b / "b.epub", title="Scanner Only B")

    s = Scanner()
    results = await s.run_once(library_name="ScanB")
    assert list(results.keys()) == ["ScanB"]
    assert results["ScanB"]["created"] == 1

    async with session_scope() as db:
        book = await get_book_by_path(db, str((lib_b / "b.epub").resolve()))
        assert book is not None
        assert book.library == "ScanB"
        # Library A was not scanned.
        assert await get_book_by_path(db, str((lib_a / "a.epub").resolve())) is None


async def test_ingest_paths_mixed_real_and_missing(two_libraries) -> None:
    lib_a, _ = two_libraries
    real = write_epub(lib_a / "webhook.epub", title="Scanner Webhook")
    missing = lib_a / "not-downloaded-yet.epub"

    s = Scanner()
    counters = await s.ingest_paths([real, missing])
    assert counters["created"] == 1
    assert counters["skipped"] == 1

    async with session_scope() as db:
        book = await get_book_by_path(db, str(real.resolve()))
        assert book is not None
        assert book.library == "ScanA"  # resolved from the configured root


async def test_handle_delete_removes_row_and_export(two_libraries, tmp_path: Path) -> None:
    lib_a, _ = two_libraries
    src = write_epub(lib_a / "doomed.epub", title="Scanner Doomed")
    s = Scanner()
    await s.ingest_paths([src])

    export = tmp_path / "doomed-export.epub"
    export.write_bytes(b"PK\x03\x04 export artifact")
    async with session_scope() as db:
        book = await get_book_by_path(db, str(src.resolve()))
        book.epub_export_path = str(export)

    await s._handle_delete(src)

    async with session_scope() as db:
        assert await get_book_by_path(db, str(src.resolve())) is None
    assert not export.exists()  # derived artifact cleaned up with the row


async def test_ingest_when_stable_waits_for_quiet_file(two_libraries, monkeypatch) -> None:
    lib_a, _ = two_libraries
    monkeypatch.setattr(scanner_mod, "STABILISE_WAIT", 0.01)
    src = write_epub(lib_a / "settling.epub", title="Scanner Settling")

    s = Scanner()
    await s._ingest_when_stable(src, "ScanA")

    async with session_scope() as db:
        assert await get_book_by_path(db, str(src.resolve())) is not None


async def test_ingest_when_stable_gives_up_on_vanished_file(two_libraries, monkeypatch) -> None:
    lib_a, _ = two_libraries
    monkeypatch.setattr(scanner_mod, "STABILISE_WAIT", 0.01)
    ghost = lib_a / "vanished.epub"

    s = Scanner()
    await s._ingest_when_stable(ghost, "ScanA")  # must simply return, no raise

    async with session_scope() as db:
        assert await get_book_by_path(db, str(ghost.resolve())) is None
