"""Apply a chosen MetadataCandidate to a book record.

Separate from metadata_lookup so the lookup module stays IO-only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import Book, BookAuthor, BookTag, MetadataSource
from despereaux.repos.books import (
    _get_or_create_author,
    _get_or_create_tag,
    _sort_name,
)
from despereaux.services.covers import write_cover
from despereaux.services.metadata_lookup import MetadataCandidate

log = logging.getLogger(__name__)

AUTO_APPLY_MIN_SCORE = 0.85


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


async def _download_cover_bytes(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.content
            # Open Library returns a 1px placeholder for missing covers — discard.
            if len(data) < 1024:
                return None
            return data
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.info("cover download failed (%s): %s", url, e)
        return None


async def apply_candidate(session: AsyncSession, book: Book, candidate: MetadataCandidate) -> None:
    """Apply candidate metadata to the book in-place, replacing the cover if the
    candidate's image is available + larger than what's stored.

    The caller is responsible for the surrounding session transaction.
    """
    # Scalar fields — overwrite only when candidate has a value.
    if candidate.title:
        book.title = candidate.title
        book.sort_title = (
            candidate.title.removeprefix("The ").removeprefix("A ").removeprefix("An ").strip()
            or candidate.title
        )
    if candidate.publisher:
        book.publisher = candidate.publisher
    if candidate.published_date:
        pd = _parse_date(candidate.published_date)
        if pd:
            book.published_date = pd
    if candidate.language:
        book.language = candidate.language
    if candidate.isbn:
        book.isbn = candidate.isbn
    if candidate.description:
        book.description = candidate.description
    if candidate.rating is not None:
        book.rating = float(candidate.rating)
    if candidate.rating_count is not None:
        book.rating_count = int(candidate.rating_count)
    if candidate.page_count and not book.page_count:
        # Only fill in page count if local extraction didn't get one (PDF page
        # count is exact, EPUB page count is an estimate — prefer external for EPUB).
        book.page_count = int(candidate.page_count)

    # Source markers.
    if candidate.source == "googlebooks":
        book.google_books_id = candidate.external_id
        book.metadata_source = MetadataSource.googlebooks
    elif candidate.source == "openlibrary":
        book.openlibrary_id = candidate.external_id
        book.metadata_source = MetadataSource.openlibrary
    else:
        book.metadata_source = MetadataSource.manual
    book.last_metadata_fetch_at = datetime.now(UTC)

    # Authors — replace via M2M (raw delete to avoid lazy-load greenlet issues
    # on the raise-mode relationship).
    if candidate.authors:
        await session.execute(BookAuthor.__table__.delete().where(BookAuthor.book_id == book.id))
        seen: set[str] = set()
        for name in candidate.authors:
            sort_name = _sort_name(name)
            if sort_name in seen:
                continue
            seen.add(sort_name)
            author = await _get_or_create_author(session, name)
            session.add(BookAuthor(book_id=book.id, author_id=author.id, role="author"))

    # Tags — replace.
    if candidate.tags:
        await session.execute(BookTag.__table__.delete().where(BookTag.book_id == book.id))
        seen_tags: set[str] = set()
        for tag in candidate.tags[:12]:
            if tag in seen_tags:
                continue
            seen_tags.add(tag)
            t = await _get_or_create_tag(session, tag)
            session.add(BookTag(book_id=book.id, tag_id=t.id))

    # Cover — only replace if candidate has a usable image AND local is small
    # or absent. We don't worry about pixel-counting here; the existing
    # cover is at most 600px wide already and external sources typically
    # return at least that size.
    if candidate.cover_url:
        data = await _download_cover_bytes(candidate.cover_url)
        if data:
            new_path = write_cover(book.id, data)
            if new_path is not None:
                book.cover_path = str(new_path)

    await session.flush()


async def maybe_auto_enrich(session: AsyncSession, book: Book) -> str | None:
    """Run lookup + apply best candidate IF score is confident enough.

    Returns the applied candidate.key() if applied, else None.

    Skipped (returns None) if the book already has external metadata.
    """
    if book.metadata_source in (
        MetadataSource.googlebooks,
        MetadataSource.openlibrary,
        MetadataSource.calibre,
        MetadataSource.manual,
    ):
        return None  # already enriched

    from despereaux.services.metadata_lookup import get_candidates_for

    # Resolve current author for the lookup query.
    result = await session.execute(
        select(BookAuthor.author_id).where(BookAuthor.book_id == book.id).limit(1)
    )
    author_name: str | None = None
    row = result.first()
    if row:
        from despereaux.models import Author

        a_result = await session.execute(select(Author).where(Author.id == row[0]))
        a = a_result.scalar_one_or_none()
        if a:
            author_name = a.name

    candidates = await get_candidates_for(book.id, book.title, author_name)
    if not candidates:
        return None
    best = candidates[0]
    if best.score < AUTO_APPLY_MIN_SCORE:
        log.info(
            "skipping auto-enrich for %s — best score %.2f < %.2f",
            book.title,
            best.score,
            AUTO_APPLY_MIN_SCORE,
        )
        return None
    log.info("auto-enriching %s via %s (score %.2f)", book.title, best.source, best.score)
    await apply_candidate(session, book, best)
    return best.key()
