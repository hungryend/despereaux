"""Regression tests for the metadata enrichment surface: candidate lookup
(Google Books + Open Library), per-book caching, candidate selection/apply,
and the auto-enrich gate.

metadata_lookup/metadata_apply construct `httpx.AsyncClient(...)` inline, so
the fixture monkeypatches `httpx.AsyncClient` with a factory that injects a
MockTransport. The ASGI test client is unaffected (it binds AsyncClient at
import time and passes its own transport). Process-global patch — keep these
tests off pytest-xdist.
"""

from __future__ import annotations

import httpx
import pytest
from PIL import Image

from despereaux.db import session_scope
from despereaux.repos.books import get_book
from despereaux.services.metadata_lookup import MetadataCandidate, _normalise, score_match
from tests.util import asgi_client, make_book_row


def _big_png() -> bytes:
    """A PNG comfortably over the 1 KB placeholder-rejection threshold.
    Noise pixels — PNG can't compress them below the limit."""
    import io
    import os

    img = Image.frombytes("RGB", (120, 180), os.urandom(120 * 180 * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    assert len(data) > 1024
    return data


_GOOGLE_ITEM = {
    "id": "gb-walk-1",
    "volumeInfo": {
        "title": "The Long Walk",
        "authors": ["Richard Bachman"],
        "publisher": "Signet",
        "publishedDate": "1979-07-02",
        "description": "One hundred boys walk until only one remains.",
        "industryIdentifiers": [
            {"type": "ISBN_10", "identifier": "0451196716"},
            {"type": "ISBN_13", "identifier": "9780451196712"},
        ],
        "imageLinks": {"thumbnail": "http://books.google.com/books/content?id=gb-walk-1"},
        "language": "en",
        "categories": ["Fiction / Horror"],
        "averageRating": 4.5,
        "ratingsCount": 1234,
        "pageCount": 384,
    },
}

_OPENLIBRARY_DOC = {
    "title": "The Long Walk",
    "key": "/works/OL999W",
    "author_name": ["Stephen King"],
    "isbn": ["9780451196712"],
    "cover_i": 55,
    "first_publish_year": 1979,
    "publisher": ["Signet"],
    "language": ["eng"],
    "subject": ["Horror", "Dystopia"],
    "number_of_pages_median": 384,
}


@pytest.fixture
def mock_external_http(monkeypatch):
    calls = {"google": 0, "openlibrary": 0, "covers": 0}
    cover_bytes = _big_png()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "www.googleapis.com":
            calls["google"] += 1
            return httpx.Response(200, json={"items": [_GOOGLE_ITEM]})
        if host == "openlibrary.org":
            calls["openlibrary"] += 1
            return httpx.Response(200, json={"docs": [_OPENLIBRARY_DOC]})
        if host in ("covers.openlibrary.org", "books.google.com"):
            calls["covers"] += 1
            return httpx.Response(200, content=cover_bytes)
        return httpx.Response(404, json={"detail": f"unmocked host {host}"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


async def test_candidates_merged_scored_and_cached(mock_external_http) -> None:
    book_id = await make_book_row(title="The Long Walk", sort_title="long walk mm1")
    async with asgi_client() as client:
        r = await client.get(f"/api/books/{book_id}/metadata-candidates")
        assert r.status_code == 200
        body = r.json()
        assert body["book"]["id"] == book_id
        keys = {c["key"] for c in body["candidates"]}
        assert keys == {"googlebooks:gb-walk-1", "openlibrary:OL999W"}
        scores = [c["score"] for c in body["candidates"]]
        assert scores == sorted(scores, reverse=True)
        assert all(s > 0.9 for s in scores)  # exact title match
        gb = next(c for c in body["candidates"] if c["source"] == "googlebooks")
        assert gb["isbn"] == "9780451196712"  # ISBN_13 preferred over ISBN_10
        assert gb["cover_url"].startswith("https://")  # http upgraded

    assert mock_external_http["google"] == 1
    assert mock_external_http["openlibrary"] == 1


async def test_candidates_second_call_hits_cache_and_refresh_bypasses(mock_external_http) -> None:
    book_id = await make_book_row(title="The Long Walk", sort_title="long walk mm2")
    async with asgi_client() as client:
        await client.get(f"/api/books/{book_id}/metadata-candidates")
        await client.get(f"/api/books/{book_id}/metadata-candidates")
        assert mock_external_http["google"] == 1  # served from the per-book JSON cache

        await client.get(f"/api/books/{book_id}/metadata-candidates", params={"refresh": 1})
        assert mock_external_http["google"] == 2


async def test_select_match_applies_metadata_and_cover(mock_external_http) -> None:
    book_id = await make_book_row(title="The Long Walk", sort_title="long walk mm3")
    async with asgi_client() as client:
        await client.get(f"/api/books/{book_id}/metadata-candidates")
        r = await client.post(
            f"/api/books/{book_id}/select-metadata-match",
            json={"source": "googlebooks", "external_id": "gb-walk-1"},
        )
        assert r.status_code == 204

    async with session_scope() as s:
        book = await get_book(s, book_id)
        assert book.isbn == "9780451196712"
        assert book.description.startswith("One hundred boys")
        assert book.rating == 4.5
        assert book.rating_count == 1234
        assert book.page_count == 384  # filled in because local extraction had none
        assert book.google_books_id == "gb-walk-1"
        assert book.metadata_source is not None and book.metadata_source.value == "googlebooks"
        assert [ba.author.name for ba in book.authors] == ["Richard Bachman"]
        assert [bt.tag.name for bt in book.tags] == ["Fiction / Horror"]
        assert book.cover_path is not None
        from pathlib import Path

        head = Path(book.cover_path).read_bytes()[:12]
        assert head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    assert mock_external_http["covers"] == 1


async def test_select_match_unknown_candidate_is_404(mock_external_http) -> None:
    book_id = await make_book_row(title="The Long Walk", sort_title="long walk mm4")
    async with asgi_client() as client:
        r = await client.post(
            f"/api/books/{book_id}/select-metadata-match",
            json={"source": "googlebooks", "external_id": "not-a-real-volume"},
        )
        assert r.status_code == 404
    # It retried with a forced refresh before giving up.
    assert mock_external_http["google"] == 2


async def test_candidates_unknown_book_is_404(mock_external_http) -> None:
    async with asgi_client() as client:
        r = await client.get("/api/books/no-such-book/metadata-candidates")
        assert r.status_code == 404
    assert mock_external_http["google"] == 0


def test_score_match_ranks_exact_above_fuzzy() -> None:
    exact = MetadataCandidate(
        source="googlebooks", external_id="a", title="The Long Walk", authors=["Richard Bachman"]
    )
    fuzzy = MetadataCandidate(
        source="googlebooks", external_id="b", title="A Walk in the Woods", authors=["Bill Bryson"]
    )
    s_exact = score_match(exact, "The Long Walk", "Richard Bachman")
    s_fuzzy = score_match(fuzzy, "The Long Walk", "Richard Bachman")
    assert s_exact > s_fuzzy
    assert s_exact > 0.95


def test_normalise_strips_subtitles_and_noise() -> None:
    assert _normalise("The Long Walk: A Novel") == "the long walk"
    assert _normalise("Dune (40th Anniversary Edition)") == "dune"
    assert _normalise("  Spaced   Out  ") == "spaced out"


async def test_auto_enrich_skips_low_score_and_already_enriched(mock_external_http) -> None:
    from despereaux.models import MetadataSource
    from despereaux.services.metadata_apply import maybe_auto_enrich

    # Low confidence: the mocked sources return "The Long Walk" for everything,
    # which can't clear the 0.85 bar against this title.
    low_id = await make_book_row(title="Zxqv Unmatchable Wordsalad Chronicle", sort_title="zxqv")
    async with session_scope() as s:
        book = await get_book(s, low_id)
        assert await maybe_auto_enrich(s, book) is None
        assert book.metadata_source == MetadataSource.local or book.metadata_source is None
        assert book.google_books_id is None

    # Already externally enriched: short-circuits without any HTTP.
    calls_before = mock_external_http["google"]
    done_id = await make_book_row(title="The Long Walk", sort_title="long walk mm5")
    async with session_scope() as s:
        book = await get_book(s, done_id)
        book.metadata_source = MetadataSource.googlebooks
        assert await maybe_auto_enrich(s, book) is None
    assert mock_external_http["google"] == calls_before
