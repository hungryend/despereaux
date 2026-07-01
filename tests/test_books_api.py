"""Regression tests for /api/books list + detail — search, library filter,
pagination, child exclusion. Titles are prefixed so the assertions stay
independent of rows other test modules seed into the shared session DB.
"""

from __future__ import annotations

from tests.util import asgi_client, make_book_row


async def test_list_search_matches_title_and_sort_title() -> None:
    await make_book_row(title="Qq Searchable Alpha", sort_title="qq 1")
    await make_book_row(title="qq searchable beta", sort_title="qq 2")
    await make_book_row(title="Unrelated Gamma", sort_title="qq searchable hidden gem")
    async with asgi_client() as client:
        r = await client.get("/api/books", params={"search": "QQ SEARCHABLE"})
        assert r.status_code == 200
        titles = [b["title"] for b in r.json()]
        # Case-insensitive; matches on either title or sort_title.
        assert "Qq Searchable Alpha" in titles
        assert "qq searchable beta" in titles
        assert "Unrelated Gamma" in titles

        r = await client.get("/api/books", params={"search": "qq-no-such-book"})
        assert r.json() == []


async def test_list_library_filter() -> None:
    await make_book_row(title="Rr LibBook One", sort_title="rr 1", library="RrShelfA")
    await make_book_row(title="Rr LibBook Two", sort_title="rr 2", library="RrShelfB")
    async with asgi_client() as client:
        r = await client.get("/api/books", params={"search": "rr libbook", "library": "RrShelfA"})
        rows = r.json()
        assert [b["title"] for b in rows] == ["Rr LibBook One"]
        assert rows[0]["library"] == "RrShelfA"


async def test_list_pagination_is_stable_by_sort_title() -> None:
    for i in (3, 1, 2):  # inserted out of order; sort_title orders the pages
        await make_book_row(title=f"Ss Paged {i}", sort_title=f"ss paged {i}")
    async with asgi_client() as client:
        page1 = await client.get(
            "/api/books", params={"search": "ss paged", "limit": 2, "offset": 0}
        )
        page2 = await client.get(
            "/api/books", params={"search": "ss paged", "limit": 2, "offset": 2}
        )
        assert [b["title"] for b in page1.json()] == ["Ss Paged 1", "Ss Paged 2"]
        assert [b["title"] for b in page2.json()] == ["Ss Paged 3"]


async def test_list_limit_bounds_rejected() -> None:
    async with asgi_client() as client:
        assert (await client.get("/api/books", params={"limit": 501})).status_code == 422
        assert (await client.get("/api/books", params={"limit": 0})).status_code == 422
        assert (await client.get("/api/books", params={"offset": -1})).status_code == 422


async def test_children_hidden_from_list_but_fetchable() -> None:
    parent_id = await make_book_row(title="Tt Campaign Book", sort_title="tt 1")
    child_id = await make_book_row(
        title="Tt Campaign Maps", sort_title="tt 2", parent_book_id=parent_id
    )
    async with asgi_client() as client:
        r = await client.get("/api/books", params={"search": "tt campaign"})
        titles = [b["title"] for b in r.json()]
        assert "Tt Campaign Book" in titles
        assert "Tt Campaign Maps" not in titles
        # The child still resolves directly.
        assert (await client.get(f"/api/books/{child_id}")).status_code == 200


async def test_detail_fields_present() -> None:
    book_id = await make_book_row(title="Uu Detail Book", sort_title="uu 1", page_count=42)
    async with asgi_client() as client:
        r = await client.get(f"/api/books/{book_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == book_id
        assert body["title"] == "Uu Detail Book"
        assert body["page_count"] == 42
        assert body["file_size"] == 10
        assert body["added_at"]
        assert body["authors"] == []
        assert body["tags"] == []


async def test_detail_unknown_is_404() -> None:
    async with asgi_client() as client:
        assert (await client.get("/api/books/no-such-id")).status_code == 404
