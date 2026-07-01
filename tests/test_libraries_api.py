"""Regression tests for /api/libraries — configured libraries with counts,
empty configured libraries, and DB-orphan surfacing."""

from __future__ import annotations

from despereaux.config import LibraryConfig, get_settings
from tests.util import asgi_client, make_book_row


async def test_configured_libraries_with_counts(tmp_path, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(
        s,
        "libraries",
        [
            LibraryConfig(name="LlFiction", path=tmp_path / "fiction"),
            LibraryConfig(name="LlEmpty", path=tmp_path / "empty"),
        ],
    )
    await make_book_row(title="Ll Book 1", library="LlFiction")
    await make_book_row(title="Ll Book 2", library="LlFiction")

    async with asgi_client() as client:
        rows = {r["name"]: r for r in (await client.get("/api/libraries")).json()}
    assert rows["LlFiction"]["book_count"] == 2
    # Configured-but-empty library still listed so the UI shows the slot.
    assert rows["LlEmpty"]["book_count"] == 0
    assert rows["LlEmpty"]["path"].endswith("empty")


async def test_orphan_library_surfaces(tmp_path, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "libraries", [LibraryConfig(name="LlConfigured", path=tmp_path / "cfg")])
    await make_book_row(title="Ll Orphan Book", library="LlGhost")

    async with asgi_client() as client:
        rows = {r["name"]: r for r in (await client.get("/api/libraries")).json()}
    assert "LlGhost" in rows
    assert rows["LlGhost"]["book_count"] == 1
    assert "orphan" in rows["LlGhost"]["path"]


async def test_children_not_counted(tmp_path, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "libraries", [LibraryConfig(name="LlNest", path=tmp_path / "nest")])
    parent = await make_book_row(title="Ll Parent", library="LlNest")
    await make_book_row(title="Ll Child", library="LlNest", parent_book_id=parent)

    async with asgi_client() as client:
        rows = {r["name"]: r for r in (await client.get("/api/libraries")).json()}
    assert rows["LlNest"]["book_count"] == 1
