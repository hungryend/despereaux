"""Regression tests for the reading-progress API — the sync backbone for the
reader UI and the Furlough Android client.

Per-user isolation rides on the authentik-header path: in the default test
config (auth_mode="authentik") an `X-authentik-username` header mints/loads
that user before the dev_mode fallback, so two headers give two real users.
"""

from __future__ import annotations

from tests.util import asgi_client, make_book_row

_ALICE = {"x-authentik-username": "zz-progress-alice"}
_BOB = {"x-authentik-username": "zz-progress-bob"}


async def test_put_get_roundtrip() -> None:
    book_id = await make_book_row(title="Pp Progress Roundtrip")
    async with asgi_client() as client:
        r = await client.put(
            f"/api/books/{book_id}/progress",
            json={"position": "epubcfi(/6/4!/2/10)", "percent": 0.37},
        )
        assert r.status_code == 204
        body = (await client.get(f"/api/books/{book_id}/progress")).json()
        assert body["book_id"] == book_id
        assert body["position"] == "epubcfi(/6/4!/2/10)"
        assert body["percent"] == 0.37
        assert body["updated_at"]


async def test_percent_clamped_to_unit_interval() -> None:
    book_id = await make_book_row(title="Pp Progress Clamp")
    async with asgi_client() as client:
        await client.put(f"/api/books/{book_id}/progress", json={"position": "p", "percent": 1.7})
        assert (await client.get(f"/api/books/{book_id}/progress")).json()["percent"] == 1.0
        await client.put(f"/api/books/{book_id}/progress", json={"position": "p", "percent": -0.2})
        assert (await client.get(f"/api/books/{book_id}/progress")).json()["percent"] == 0.0


async def test_put_unknown_book_is_404_and_empty_get_is_null() -> None:
    book_id = await make_book_row(title="Pp Progress Empty")
    async with asgi_client() as client:
        r = await client.put(
            "/api/books/no-such-book/progress", json={"position": "p", "percent": 0.5}
        )
        assert r.status_code == 404
        r = await client.get(f"/api/books/{book_id}/progress")
        assert r.status_code == 200
        assert r.json() is None


async def test_upsert_overwrites_previous_position() -> None:
    book_id = await make_book_row(title="Pp Progress Upsert")
    async with asgi_client() as client:
        await client.put(f"/api/books/{book_id}/progress", json={"position": "a", "percent": 0.1})
        await client.put(f"/api/books/{book_id}/progress", json={"position": "b", "percent": 0.9})
        body = (await client.get(f"/api/books/{book_id}/progress")).json()
        assert body["position"] == "b"
        assert body["percent"] == 0.9


async def test_progress_list_returns_all_rows_for_caller() -> None:
    b1 = await make_book_row(title="Pp List One")
    b2 = await make_book_row(title="Pp List Two")
    async with asgi_client() as client:
        await client.put(
            f"/api/books/{b1}/progress", json={"position": "x", "percent": 0.2}, headers=_ALICE
        )
        await client.put(
            f"/api/books/{b2}/progress", json={"position": "y", "percent": 0.4}, headers=_ALICE
        )
        rows = (await client.get("/api/progress", headers=_ALICE)).json()
        by_book = {r["book_id"]: r for r in rows}
        assert by_book[b1]["percent"] == 0.2
        assert by_book[b2]["percent"] == 0.4


async def test_progress_is_isolated_per_user() -> None:
    book_id = await make_book_row(title="Pp Isolation")
    async with asgi_client() as client:
        await client.put(
            f"/api/books/{book_id}/progress",
            json={"position": "alice-was-here", "percent": 0.5},
            headers=_ALICE,
        )
        # Bob sees no progress on the same book...
        assert (await client.get(f"/api/books/{book_id}/progress", headers=_BOB)).json() is None
        # ...and his list doesn't contain Alice's row.
        bob_books = {r["book_id"] for r in (await client.get("/api/progress", headers=_BOB)).json()}
        assert book_id not in bob_books
        # Alice still reads hers back.
        mine = (await client.get(f"/api/books/{book_id}/progress", headers=_ALICE)).json()
        assert mine["position"] == "alice-was-here"


async def test_progress_validation_rejects_missing_fields() -> None:
    book_id = await make_book_row(title="Pp Validation")
    async with asgi_client() as client:
        r = await client.put(f"/api/books/{book_id}/progress", json={"percent": 0.5})
        assert r.status_code == 422
