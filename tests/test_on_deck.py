"""Tests for the library "On deck" shelf + mark-as-unread (clear progress).

Drives the real web routes via the ASGI app. DESPEREAUX_DEV_MODE=true (set in
conftest) auto-authenticates as a devuser, so the progress rows created via PUT
attribute to the same user the GET / POST routes read back.
"""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from despereaux.db import session_scope
from despereaux.main import app
from despereaux.models import Book
from despereaux.models.base import new_id


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_book(title: str) -> str:
    async with session_scope() as s:
        b = Book(
            id=new_id(),
            title=title,
            sort_title=title,
            format="epub",
            library="Default",
            file_path=f"/tmp/{new_id()}.epub",
            file_size=10,
            file_mtime=datetime.now(UTC),
            file_hash=new_id().replace("-", ""),
        )
        s.add(b)
        await s.flush()
        return b.id


async def test_on_deck_appears_then_cleared() -> None:
    book_id = await _make_book("On Deck Alpha")
    clear_action = f"/book/{book_id}/progress/clear"
    async with _client() as client:
        # No progress yet -> this book isn't on the shelf.
        r = await client.get("/")
        assert r.status_code == 200
        assert clear_action not in r.text

        # Record a reading position -> the book joins the On-deck shelf.
        r = await client.put(
            f"/api/books/{book_id}/progress",
            json={"position": "epubcfi(/6/2!/4)", "percent": 0.42},
        )
        assert r.status_code == 204

        r = await client.get("/")
        assert r.status_code == 200
        assert 'class="on-deck"' in r.text
        assert clear_action in r.text  # the menu's mark-as-unread form rendered

        # Mark as unread -> redirect, and the book leaves the shelf.
        r = await client.post(clear_action, data={"next": "/"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"

        r = await client.get("/")
        assert clear_action not in r.text


async def test_on_deck_hidden_during_search() -> None:
    book_id = await _make_book("On Deck Bravo")
    async with _client() as client:
        await client.put(
            f"/api/books/{book_id}/progress",
            json={"position": "p", "percent": 0.5},
        )
        # Shelf shows on the home view...
        r = await client.get("/")
        assert 'class="on-deck"' in r.text
        # ...but is suppressed while searching (the grid is a focused result set).
        r = await client.get("/", params={"search": "Bravo"})
        assert r.status_code == 200
        assert 'class="on-deck"' not in r.text


async def test_first_page_excluded_from_on_deck() -> None:
    """A book only opened to its first page (~0%) stays in the library but is not
    on the On-deck shelf; one being actively read is."""
    opened = await _make_book("On Deck Delta (opened)")
    reading = await _make_book("On Deck Echo (reading)")
    async with _client() as client:
        await client.put(f"/api/books/{opened}/progress", json={"position": "0", "percent": 0.0})
        await client.put(f"/api/books/{reading}/progress", json={"position": "p", "percent": 0.2})
        r = await client.get("/")
        assert r.status_code == 200
        # Barely-opened: present in the library grid, absent from the shelf menu.
        assert f'href="/book/{opened}"' in r.text
        assert f"/book/{opened}/progress/clear" not in r.text
        # Actively reading: on the shelf.
        assert f"/book/{reading}/progress/clear" in r.text


async def test_clear_progress_rejects_offsite_next() -> None:
    """The post-action redirect can't be steered off-site (open-redirect guard)."""
    book_id = await _make_book("On Deck Charlie")
    async with _client() as client:
        await client.put(f"/api/books/{book_id}/progress", json={"position": "p", "percent": 0.1})
        r = await client.post(
            f"/book/{book_id}/progress/clear",
            data={"next": "https://evil.example/phish"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/"
