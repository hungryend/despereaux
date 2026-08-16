"""Tests for the library "On deck" shelf + mark-as-unread (clear progress).

Drives the real web routes via the ASGI app. DESPEREAUX_DEV_MODE=true (set in
conftest) auto-authenticates as a devuser, so the progress rows created via PUT
attribute to the same user the GET / POST routes read back.
"""

from __future__ import annotations

import asyncio
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


async def test_finished_book_leaves_on_deck() -> None:
    """Reading to the last page (100%) takes a book off the shelf — it's read, not
    "continue reading". A book still mid-way stays."""
    done = await _make_book("On Deck Foxtrot (finished)")
    reading = await _make_book("On Deck Golf (reading)")
    async with _client() as client:
        # Mid-book: on the shelf.
        await client.put(f"/api/books/{done}/progress", json={"position": "p", "percent": 0.5})
        r = await client.get("/")
        assert f"/book/{done}/progress/clear" in r.text

        # Read to the last page -> off the shelf, still in the library grid.
        await client.put(f"/api/books/{done}/progress", json={"position": "end", "percent": 1.0})
        await client.put(f"/api/books/{reading}/progress", json={"position": "p", "percent": 0.3})
        r = await client.get("/")
        assert r.status_code == 200
        assert f"/book/{done}/progress/clear" not in r.text
        assert f'href="/book/{done}"' in r.text
        assert f"/book/{reading}/progress/clear" in r.text


async def test_epub_near_complete_counts_as_finished() -> None:
    """EPUB CFI percentages can stop a hair short of 1.0 on the final page, so the
    last 1% counts as read — otherwise a finished EPUB is pinned to the shelf."""
    almost = await _make_book("On Deck Hotel (99.5%)")
    not_yet = await _make_book("On Deck India (97%)")
    async with _client() as client:
        await client.put(f"/api/books/{almost}/progress", json={"position": "p", "percent": 0.995})
        await client.put(f"/api/books/{not_yet}/progress", json={"position": "p", "percent": 0.97})
        r = await client.get("/")
        assert f"/book/{almost}/progress/clear" not in r.text
        # Genuinely short of the end -> still on the shelf.
        assert f"/book/{not_yet}/progress/clear" in r.text


async def test_mark_as_read_removes_from_on_deck() -> None:
    """The explicit "Mark as read" action pins progress to 100% and clears the
    shelf entry, keeping the saved position for a later re-read."""
    book_id = await _make_book("On Deck Juliett")
    finish_action = f"/book/{book_id}/progress/finish"
    async with _client() as client:
        await client.put(
            f"/api/books/{book_id}/progress",
            json={"position": "epubcfi(/6/2!/4)", "percent": 0.42},
        )
        r = await client.get("/")
        assert finish_action in r.text  # the menu's mark-as-read form rendered

        r = await client.post(finish_action, data={"next": "/"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"

        r = await client.get("/")
        assert finish_action not in r.text

        # Progress is kept at 100% with the position intact (not deleted).
        prog = (await client.get(f"/api/books/{book_id}/progress")).json()
        assert prog["percent"] == 1.0
        assert prog["position"] == "epubcfi(/6/2!/4)"


async def test_mark_as_read_without_prior_progress() -> None:
    """A book read elsewhere can be marked read without ever being opened here."""
    book_id = await _make_book("On Deck Kilo")
    async with _client() as client:
        r = await client.post(
            f"/book/{book_id}/progress/finish",
            data={"next": f"/book/{book_id}"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        prog = (await client.get(f"/api/books/{book_id}/progress")).json()
        assert prog["percent"] == 1.0
        # Never opened here, so there's no position to resume from.
        assert prog["position"] == ""
        # And it doesn't appear on the shelf.
        r = await client.get("/")
        assert f"/book/{book_id}/progress/clear" not in r.text


async def test_mark_as_read_unknown_book_404s() -> None:
    async with _client() as client:
        r = await client.post(
            "/book/does-not-exist/progress/finish", data={"next": "/"}, follow_redirects=False
        )
        assert r.status_code == 404


async def test_finish_rejects_offsite_next() -> None:
    """Mark-as-read shares the clear route's open-redirect guard."""
    book_id = await _make_book("On Deck Lima")
    async with _client() as client:
        r = await client.post(
            f"/book/{book_id}/progress/finish",
            data={"next": "https://evil.example/phish"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/"


async def test_book_detail_shows_finished_state() -> None:
    """The detail page renders in each state: unread (offer to mark read), part-read
    (percent + both actions), finished (says so, no stale "100%" + mark-read)."""
    book_id = await _make_book("On Deck November")
    finish_action = f"/book/{book_id}/progress/finish"
    clear_action = f"/book/{book_id}/progress/clear"
    async with _client() as client:
        # Never opened: can still be marked read, nothing to mark unread.
        r = await client.get(f"/book/{book_id}")
        assert r.status_code == 200
        assert finish_action in r.text
        assert clear_action not in r.text

        # Part-read: shows the percentage and both actions.
        await client.put(f"/api/books/{book_id}/progress", json={"position": "p", "percent": 0.42})
        r = await client.get(f"/book/{book_id}")
        assert "42%" in r.text
        assert finish_action in r.text
        assert clear_action in r.text

        # Finished: reads as finished, and stops offering "mark as read".
        await client.post(finish_action, data={"next": f"/book/{book_id}"})
        r = await client.get(f"/book/{book_id}")
        assert r.status_code == 200
        assert "Finished" in r.text
        assert finish_action not in r.text
        assert clear_action in r.text  # still re-readable


async def test_progress_updated_at_advances_on_resave() -> None:
    """The shelf orders by most-recently-read, so a re-save must move updated_at.
    An upsert's DO UPDATE clause doesn't fire the column's `onupdate`, so this
    guards the explicit SET."""
    book_id = await _make_book("On Deck Mike")
    async with _client() as client:
        await client.put(f"/api/books/{book_id}/progress", json={"position": "a", "percent": 0.1})
        first = (await client.get(f"/api/books/{book_id}/progress")).json()["updated_at"]
        await asyncio.sleep(1.1)  # server_default now() has 1-second resolution
        await client.put(f"/api/books/{book_id}/progress", json={"position": "b", "percent": 0.2})
        second = (await client.get(f"/api/books/{book_id}/progress")).json()["updated_at"]
    assert second > first


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
