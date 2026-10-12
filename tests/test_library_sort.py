"""Library sort/group views: the sort bar offers Title A-Z / By author / By series
/ Recently added. ?sort=author groups books under each of their authors (a
co-authored book appears in every author's section); ?sort=series stacks each
series into one tile that opens /series/{id}. The pick is remembered per
profile, so each test signs in as its own `X-authentik-username` user."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from despereaux.db import session_scope
from despereaux.main import app
from despereaux.models.base import new_id
from despereaux.repos import books as books_repo
from despereaux.services.ingest import ingest_file
from tests.util import write_epub


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _as(name: str) -> dict[str, str]:
    """A fresh non-admin profile (no saved sort yet)."""
    return {"x-authentik-username": f"zz-sort-{name}"}


async def _seed(
    title: str,
    authors: list[str],
    *,
    series: str | None = None,
    index: float | None = None,
) -> str:
    async with session_scope() as s:
        book = await books_repo.upsert_book(
            s,
            fields={
                "title": title,
                "sort_title": title.lower(),
                "format": "epub",
                "library": "Default",
                "file_path": f"/tmp/{new_id()}.epub",
                "file_size": 1,
                "file_mtime": datetime.now(UTC),
                "file_hash": new_id().replace("-", ""),
            },
            author_names=authors,
            series_name=series,
            series_index=index,
            tag_names=[],
        )
        return book.id


async def _series_id_of(book_id: str) -> str:
    async with session_scope() as s:
        return (await books_repo.get_book(s, book_id)).series_id


async def test_sort_bar_default_is_flat_title() -> None:
    await _seed("Sortbar Probe", ["Solo Writer"])
    async with _client() as client:
        r = await client.get("/", headers=_as("default"))
        assert r.status_code == 200
        assert 'class="sort-bar"' in r.text
        assert 'class="sort-opt active" href="/?sort=title"' in r.text
        assert 'class="author-bar"' not in r.text  # flat grid, no grouping
        assert "series-card" not in r.text


async def test_sort_author_groups_books_under_each_author() -> None:
    await _seed("Alpha Tale", ["Shared Author"])
    await _seed("Beta Tale", ["Shared Author"])
    collab = await _seed("Collab Tale", ["Anna Able", "Zoe Zane"])
    async with _client() as client:
        r = await client.get("/", params={"sort": "author"}, headers=_as("author"))
        assert r.status_code == 200
        body = r.text
        # Author section bars render.
        assert 'class="author-bar">Shared Author' in body
        assert 'class="author-bar">Anna Able' in body
        assert 'class="author-bar">Zoe Zane' in body
        # The co-authored book is listed under BOTH of its authors.
        assert body.count(f'/book/{collab}"') == 2


async def test_sort_invalid_falls_back_to_title() -> None:
    await _seed("Fallback Probe", ["Some One"])
    async with _client() as client:
        r = await client.get("/", params={"sort": "bogus"}, headers=_as("bogus"))
        assert r.status_code == 200
        assert 'class="sort-opt active" href="/?sort=title"' in r.text
        assert 'class="author-bar"' not in r.text


async def test_sort_added_is_active_and_renders() -> None:
    await _seed("Recent Probe", ["Date Author"])
    async with _client() as client:
        r = await client.get("/", params={"sort": "added"}, headers=_as("added"))
        assert r.status_code == 200
        assert 'class="sort-opt active" href="/?sort=added"' in r.text


async def test_sort_series_stacks_series_and_keeps_singles_as_books() -> None:
    first = await _seed("Stack One", ["Stack Writer"], series="Stackwork Saga", index=1)
    second = await _seed("Stack Two", ["Stack Writer"], series="Stackwork Saga", index=2)
    lonely = await _seed("Lonely Stack Volume", ["Stack Writer"], series="Solo Stackwork", index=3)
    standalone = await _seed("Standalone Stackwork", ["Stack Writer"])
    series_id = await _series_id_of(first)
    async with _client() as client:
        r = await client.get(
            "/", params={"sort": "series", "search": "stack"}, headers=_as("series")
        )
        assert r.status_code == 200
        body = r.text
        assert 'class="sort-opt active" href="/?sort=series' in body
        # The two-book series is one stack tile, not two book cards.
        assert f'href="/series/{series_id}"' in body
        assert "2 books" in body
        assert f'/book/{first}"' not in body
        assert f'/book/{second}"' not in body
        # A one-book series and a standalone book stay as normal book tiles.
        assert f'/book/{lonely}"' in body
        assert "Solo Stackwork #3" in body
        assert f'/book/{standalone}"' in body


async def test_series_page_lists_books_in_reading_order() -> None:
    third = await _seed("Zeta Opener", ["Order Writer"], series="Ordering Cycle", index=3)
    first = await _seed("Yonder Start", ["Order Writer"], series="Ordering Cycle", index=1)
    half = await _seed("Xylo Interlude", ["Order Writer"], series="Ordering Cycle", index=1.5)
    unnumbered = await _seed("Alpha Extra", ["Order Writer"], series="Ordering Cycle")
    series_id = await _series_id_of(first)
    async with _client() as client:
        r = await client.get(f"/series/{series_id}", headers=_as("series-page"))
        assert r.status_code == 200
        body = r.text
        assert "Ordering Cycle" in body
        positions = [body.index(f'/book/{b}"') for b in (first, half, third, unnumbered)]
        assert positions == sorted(positions)
        assert "#1.5" in body
        assert "Unnumbered" in body
        assert (await client.get("/series/no-such-series")).status_code == 404


async def test_sort_choice_is_remembered_per_profile() -> None:
    await _seed("Memory Probe", ["Memory Writer"], series="Memory Line", index=1)
    alice, bob = _as("alice"), _as("bob")
    async with _client() as client:
        await client.get("/", params={"sort": "series"}, headers=alice)
        await client.get("/", params={"sort": "added"}, headers=bob)
        # A plain visit reopens each profile's own last pick.
        r = await client.get("/", headers=alice)
        assert 'class="sort-opt active" href="/?sort=series"' in r.text
        r = await client.get("/", headers=bob)
        assert 'class="sort-opt active" href="/?sort=added"' in r.text
        # Picking Title A-Z goes back to the flat default and sticks.
        await client.get("/", params={"sort": "title"}, headers=alice)
        r = await client.get("/", headers=alice)
        assert 'class="sort-opt active" href="/?sort=title"' in r.text


async def test_admin_sets_and_clears_series_by_hand() -> None:
    book_id = await _seed("Handset Book", ["Hand Writer"])
    async with _client() as client:
        r = await client.post(
            f"/book/{book_id}/series", data={"series_name": "Handmade Arc", "series_index": "2"}
        )
        assert r.status_code == 303
        async with session_scope() as s:
            book = await books_repo.get_book(s, book_id)
            assert book.series.name == "Handmade Arc"
            assert book.series_index == 2.0
        page = (await client.get(f"/book/{book_id}")).text
        assert f'href="/series/{book.series_id}"' in page
        assert "Change series" in page

        r = await client.post(f"/book/{book_id}/series", data={"series_name": "  "})
        assert r.status_code == 303
        async with session_scope() as s:
            book = await books_repo.get_book(s, book_id)
            assert book.series_id is None
            assert book.series_index is None

        bad = await client.post(
            f"/book/{book_id}/series", data={"series_name": "X", "series_index": "two"}
        )
        assert bad.status_code == 400


async def test_series_api_is_admin_only() -> None:
    book_id = await _seed("Api Series Book", ["Api Writer"])
    async with _client() as client:
        r = await client.put(
            f"/api/books/{book_id}/series",
            json={"name": "Scripted Arc", "index": 4},
            headers=_as("api-reader"),
        )
        assert r.status_code == 403
        r = await client.post(
            f"/book/{book_id}/series", data={"series_name": "Nope"}, headers=_as("api-reader")
        )
        assert r.status_code == 403
        r = await client.put(
            f"/api/books/{book_id}/series", json={"name": "Scripted Arc", "index": 4}
        )
        assert r.status_code == 200
        assert r.json()["series"]["name"] == "Scripted Arc"
        assert r.json()["series_index"] == 4.0
        assert (
            "Set series"
            not in (await client.get(f"/book/{book_id}", headers=_as("api-reader"))).text
        )


async def test_manual_series_survives_reingest_without_series_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    # No network: stub external enrichment at its source (as test_ingest_epub_pdf does).
    async def _noop(session, book):
        return None

    import despereaux.services.metadata_apply as metadata_apply

    monkeypatch.setattr(metadata_apply, "maybe_auto_enrich", _noop)
    path = write_epub(tmp_path / "manual-series.epub", title="Reingest Keeper")
    book_id, _ = await ingest_file(path)
    async with session_scope() as s:
        book = await books_repo.get_book(s, book_id)
        await books_repo.set_series(s, book, "Kept By Hand", 5)
    # Changed content (new hash) re-runs the upsert; the file still has no series.
    write_epub(path, title="Reingest Keeper", chapters=3)
    again, _ = await ingest_file(path)
    assert again == book_id  # same row, upserted by path
    async with session_scope() as s:
        book = await books_repo.get_book(s, book_id)
        assert book.series.name == "Kept By Hand"
        assert book.series_index == 5.0
    # A file that DOES carry series metadata wins over the manual value.
    write_epub(path, title="Reingest Keeper", series="From The File", series_index=1)
    await ingest_file(path)
    async with session_scope() as s:
        book = await books_repo.get_book(s, book_id)
        assert book.series.name == "From The File"
