"""Library-editing actions are admin-only.

Every signed-in profile can read, track progress and convert, but removing a
book, editing its metadata, attaching/detaching assets and replacing or
removing an EPUB export are admin actions. A non-admin is minted the same way
as in test_progress_api: an `X-authentik-username` header with no admin group
(default test config is auth_mode="authentik"); a headerless request is the
dev-mode admin `devuser`.
"""

from __future__ import annotations

import pytest

from despereaux.db import session_scope
from despereaux.repos import books as books_repo
from tests.util import asgi_client, make_book_row

_READER = {"x-authentik-username": "zz-gating-reader"}
_ADMIN_BY_GROUP = {
    "x-authentik-username": "zz-gating-admin",
    "x-authentik-groups": "ebook-admin",
}


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("post", "/book/{id}/delete", None),
        ("post", "/book/{id}/attach", {"parent_id": "{parent}"}),
        ("post", "/book/{id}/detach", None),
        ("post", "/book/{id}/convert/delete", None),
        ("post", "/book/{id}/convert?force=1", None),
        ("post", "/book/{id}/metadata/refresh", None),
        ("post", "/book/{id}/metadata/select", {"source": "openlibrary", "external_id": "x"}),
        ("get", "/book/{id}/metadata", None),
        ("get", "/book/{id}/attach", None),
    ],
)
async def test_non_admin_gets_403_on_web_edit_routes(method, path, data) -> None:
    book_id = await make_book_row(title="Gating Target")
    parent_id = await make_book_row(title="Gating Parent")
    url = path.format(id=book_id)
    if data:
        data = {k: v.format(parent=parent_id) for k, v in data.items()}
    async with asgi_client() as client:
        r = await client.request(method.upper(), url, data=data, headers=_READER)
    assert r.status_code == 403, (url, r.status_code, r.text[:200])
    # Nothing happened to the book.
    async with session_scope() as s:
        book = await books_repo.get_book(s, book_id)
        assert book is not None
        assert book.parent_book_id is None


async def test_non_admin_gets_403_on_api_edit_routes() -> None:
    book_id = await make_book_row(title="Gating Api Target", fmt="pdf")
    async with asgi_client() as client:
        r = await client.post(
            f"/api/books/{book_id}/select-metadata-match",
            json={"source": "openlibrary", "external_id": "x"},
            headers=_READER,
        )
        assert r.status_code == 403
        r = await client.post(f"/api/books/{book_id}/convert?force=1", headers=_READER)
        assert r.status_code == 403


async def test_admin_can_still_remove_and_attach() -> None:
    child = await make_book_row(title="Gating Child")
    parent = await make_book_row(title="Gating Adult")
    doomed = await make_book_row(title="Gating Doomed")
    async with asgi_client() as client:
        r = await client.post(f"/book/{child}/attach", data={"parent_id": parent})
        assert r.status_code == 303
        # An admin via the Authentik group (not just dev-mode devuser) passes too.
        r = await client.post(f"/book/{doomed}/delete", headers=_ADMIN_BY_GROUP)
        assert r.status_code == 303
    async with session_scope() as s:
        assert (await books_repo.get_book(s, child)).parent_book_id == parent
        assert await books_repo.get_book(s, doomed) is None


async def test_book_page_hides_admin_controls_from_non_admin() -> None:
    book_id = await make_book_row(title="Gating Detail")
    async with asgi_client() as client:
        reader = (await client.get(f"/book/{book_id}", headers=_READER)).text
        admin = (await client.get(f"/book/{book_id}")).text
    for marker in ("Edit metadata", "Remove from library", "Attach as asset of"):
        assert marker not in reader
        assert marker in admin
    # Reading stays available to everyone.
    assert f'href="/read/{book_id}"' in reader


async def test_non_admin_can_still_mark_progress() -> None:
    book_id = await make_book_row(title="Gating Reader Ok")
    async with asgi_client() as client:
        r = await client.post(
            f"/book/{book_id}/progress/finish", data={"next": "/"}, headers=_READER
        )
        assert r.status_code == 303
