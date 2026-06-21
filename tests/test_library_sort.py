"""Library sort/group views: the sort bar offers Title A-Z / By author / Recently
added, and ?sort=author groups books under each of their authors (a co-authored
book appears in every author's section)."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from despereaux.db import session_scope
from despereaux.main import app
from despereaux.models.base import new_id
from despereaux.repos import books as books_repo


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed(title: str, authors: list[str]) -> str:
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
            series_name=None,
            series_index=None,
            tag_names=[],
        )
        return book.id


async def test_sort_bar_default_is_flat_title() -> None:
    await _seed("Sortbar Probe", ["Solo Writer"])
    async with _client() as client:
        r = await client.get("/")
        assert r.status_code == 200
        assert 'class="sort-bar"' in r.text
        assert 'class="sort-opt active" href="/?sort=title"' in r.text
        assert 'class="author-bar"' not in r.text  # flat grid, no grouping


async def test_sort_author_groups_books_under_each_author() -> None:
    await _seed("Alpha Tale", ["Shared Author"])
    await _seed("Beta Tale", ["Shared Author"])
    collab = await _seed("Collab Tale", ["Anna Able", "Zoe Zane"])
    async with _client() as client:
        r = await client.get("/", params={"sort": "author"})
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
        r = await client.get("/", params={"sort": "bogus"})
        assert r.status_code == 200
        assert 'class="sort-opt active" href="/?sort=title"' in r.text
        assert 'class="author-bar"' not in r.text


async def test_sort_added_is_active_and_renders() -> None:
    await _seed("Recent Probe", ["Date Author"])
    async with _client() as client:
        r = await client.get("/", params={"sort": "added"})
        assert r.status_code == 200
        assert 'class="sort-opt active" href="/?sort=added"' in r.text
