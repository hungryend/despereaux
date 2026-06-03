"""External metadata enrichment.

Queries Google Books + Open Library, scores candidates by fuzzy title+author
match (rapidfuzz), and returns a deduplicated, ranked list. The picker UI
shows these and lets the user pick the right one when the auto-match is
wrong.

Cached per-book to /config/metadata-cache/<book_id>.json with a 30-day TTL.
Both APIs work unauthenticated; Google Books supports an optional API key
(DESPEREAUX_GOOGLE_BOOKS_API_KEY) to raise rate limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from rapidfuzz import fuzz

from despereaux.config import get_settings

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
HTTP_TIMEOUT = 8.0
MAX_PER_SOURCE = 8


@dataclass
class MetadataCandidate:
    """One possible metadata match from a remote source."""

    source: str  # 'googlebooks' | 'openlibrary'
    external_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    publisher: str | None = None
    published_date: str | None = None
    description: str | None = None
    isbn: str | None = None
    language: str | None = None
    cover_url: str | None = None
    tags: list[str] = field(default_factory=list)
    rating: float | None = None
    rating_count: int | None = None
    page_count: int | None = None
    score: float = 0.0  # match confidence 0..1

    def key(self) -> str:
        """Stable identity for de-dup + selection."""
        return f"{self.source}:{self.external_id}"


# ---------- Google Books ----------


def _gb_to_candidate(item: dict) -> MetadataCandidate | None:
    info = item.get("volumeInfo") or {}
    title = (info.get("title") or "").strip()
    if not title:
        return None

    # ISBNs come in industryIdentifiers list
    isbn = None
    for ident in info.get("industryIdentifiers") or []:
        if ident.get("type") == "ISBN_13":
            isbn = ident.get("identifier")
            break
        if ident.get("type") == "ISBN_10" and not isbn:
            isbn = ident.get("identifier")

    images = info.get("imageLinks") or {}
    # Prefer larger; Google supports zoom=0..5
    cover = (
        images.get("extraLarge")
        or images.get("large")
        or images.get("medium")
        or images.get("small")
        or images.get("thumbnail")
        or images.get("smallThumbnail")
    )
    if cover and cover.startswith("http://"):
        cover = "https://" + cover[len("http://") :]

    rating = info.get("averageRating")
    rating_count = info.get("ratingsCount")

    return MetadataCandidate(
        source="googlebooks",
        external_id=item.get("id", ""),
        title=title,
        authors=list(info.get("authors") or []),
        publisher=info.get("publisher"),
        published_date=info.get("publishedDate"),
        description=info.get("description"),
        isbn=isbn,
        language=info.get("language"),
        cover_url=cover,
        tags=list(info.get("categories") or []),
        rating=float(rating) if rating is not None else None,
        rating_count=int(rating_count) if rating_count is not None else None,
        page_count=info.get("pageCount"),
    )


async def search_google_books(
    title: str, author: str | None = None, *, max_results: int = MAX_PER_SOURCE
) -> list[MetadataCandidate]:
    s = get_settings()
    q_parts = []
    if title:
        q_parts.append(f'intitle:"{title}"')
    if author:
        q_parts.append(f'inauthor:"{author}"')
    if not q_parts:
        return []
    params: dict[str, str | int] = {
        "q": "+".join(q_parts),
        "maxResults": max_results,
        "printType": "books",
    }
    if s.google_books_api_key:
        params["key"] = s.google_books_api_key

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get("https://www.googleapis.com/books/v1/volumes", params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("Google Books search failed: %s", e)
        return []

    items = data.get("items") or []
    out: list[MetadataCandidate] = []
    for item in items:
        c = _gb_to_candidate(item)
        if c:
            out.append(c)
    return out


async def fetch_google_books_volume(volume_id: str) -> MetadataCandidate | None:
    """Direct-by-ID lookup. Used as a fallback when a candidate the user
    selected isn't in the per-book cache (e.g. came from a one-off manual search)."""
    s = get_settings()
    params: dict[str, str] = {}
    if s.google_books_api_key:
        params["key"] = s.google_books_api_key
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(
                f"https://www.googleapis.com/books/v1/volumes/{volume_id}", params=params
            )
            r.raise_for_status()
            item = r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("Google Books volume fetch failed (%s): %s", volume_id, e)
        return None
    return _gb_to_candidate(item)


# ---------- Open Library ----------


def _ol_cover_url(cover_id: int | None, isbn: str | None) -> str | None:
    if cover_id:
        return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
    if isbn:
        return f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
    return None


def _ol_to_candidate(doc: dict) -> MetadataCandidate | None:
    title = (doc.get("title") or "").strip()
    if not title:
        return None
    work_key = doc.get("key") or ""  # e.g. "/works/OL12345W"
    olid = work_key.split("/")[-1] if work_key else ""

    isbn_list = doc.get("isbn") or []
    isbn = next((i for i in isbn_list if len(i) == 13), None) or next(iter(isbn_list), None)

    cover_id = doc.get("cover_i")
    cover = _ol_cover_url(cover_id, isbn)

    first_year = doc.get("first_publish_year")
    return MetadataCandidate(
        source="openlibrary",
        external_id=olid,
        title=title,
        authors=list(doc.get("author_name") or []),
        publisher=(doc.get("publisher") or [None])[0],
        published_date=str(first_year) if first_year else None,
        description=None,  # not in search results; would need a second fetch
        isbn=isbn,
        language=(doc.get("language") or [None])[0],
        cover_url=cover,
        tags=list(doc.get("subject") or [])[:8],
        page_count=doc.get("number_of_pages_median"),
    )


async def fetch_open_library_work(olid: str) -> MetadataCandidate | None:
    """Look up a single Open Library work by OLID. Authors are referenced
    by key (`/authors/OL...A`) — we resolve one author hop for the display
    name; deeper resolution would be a lot of extra HTTP for little value."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(f"https://openlibrary.org/works/{olid}.json")
            r.raise_for_status()
            work = r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("Open Library work fetch failed (%s): %s", olid, e)
        return None

    title = (work.get("title") or "").strip()
    if not title:
        return None
    # description can be a string OR {"type":"/type/text","value":"..."}
    desc_raw = work.get("description")
    desc = desc_raw.get("value") if isinstance(desc_raw, dict) else desc_raw
    cover_id = (work.get("covers") or [None])[0]
    cover = _ol_cover_url(cover_id, None)
    subjects = list(work.get("subjects") or [])[:8]
    first_publish = work.get("first_publish_date")

    # Resolve up to 2 author names — sequential, but capped.
    authors: list[str] = []
    for a in (work.get("authors") or [])[:2]:
        akey = (a.get("author") or {}).get("key") if isinstance(a, dict) else None
        if not akey:
            continue
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                ar = await client.get(f"https://openlibrary.org{akey}.json")
                ar.raise_for_status()
                name = (ar.json() or {}).get("name")
                if name:
                    authors.append(name)
        except (httpx.HTTPError, httpx.TimeoutException):
            continue

    return MetadataCandidate(
        source="openlibrary",
        external_id=olid,
        title=title,
        authors=authors,
        publisher=None,
        published_date=first_publish,
        description=desc,
        isbn=None,
        cover_url=cover,
        tags=subjects,
    )


async def fetch_candidate_by_id(source: str, external_id: str) -> MetadataCandidate | None:
    """Resolve a candidate from its source + ID. Used as the canonical
    lookup when a user selects something we don't have cached."""
    if source == "googlebooks":
        return await fetch_google_books_volume(external_id)
    if source == "openlibrary":
        return await fetch_open_library_work(external_id)
    return None


async def search_open_library(
    title: str, author: str | None = None, *, max_results: int = MAX_PER_SOURCE
) -> list[MetadataCandidate]:
    params: dict[str, str | int] = {"limit": max_results}
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get("https://openlibrary.org/search.json", params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("Open Library search failed: %s", e)
        return []

    docs = (data.get("docs") or [])[:max_results]
    out: list[MetadataCandidate] = []
    for d in docs:
        c = _ol_to_candidate(d)
        if c:
            out.append(c)
    return out


# ---------- Scoring + dedup ----------


_TITLE_NOISE = re.compile(r"[\(\[].*?[\)\]]|[:\-–—]\s.*$", re.IGNORECASE)  # noqa: RUF001


def _normalise(s: str) -> str:
    if not s:
        return ""
    s = _TITLE_NOISE.sub("", s)  # drop subtitle / parenthetical
    return re.sub(r"\s+", " ", s).strip().casefold()


def score_match(
    candidate: MetadataCandidate, source_title: str, source_author: str | None
) -> float:
    """0..1 confidence that this candidate matches the local book."""
    t_score = fuzz.token_set_ratio(_normalise(candidate.title), _normalise(source_title)) / 100.0
    if not source_author or not candidate.authors:
        # No author signal — title alone, slight cap.
        return min(t_score, 0.95)
    best_a = max(
        fuzz.token_set_ratio(_normalise(source_author), _normalise(a)) / 100.0
        for a in candidate.authors
    )
    # Title is more decisive, author confirms.
    return round(0.7 * t_score + 0.3 * best_a, 4)


async def find_candidates(title: str, author: str | None = None) -> list[MetadataCandidate]:
    """Hit both sources in parallel, score + sort, dedup by key."""
    gb_task = asyncio.create_task(search_google_books(title, author))
    ol_task = asyncio.create_task(search_open_library(title, author))
    gb, ol = await asyncio.gather(gb_task, ol_task, return_exceptions=False)
    all_candidates: list[MetadataCandidate] = list(gb) + list(ol)
    for c in all_candidates:
        c.score = score_match(c, title, author)
    seen: set[str] = set()
    deduped: list[MetadataCandidate] = []
    for c in sorted(all_candidates, key=lambda x: -x.score):
        if c.key() in seen:
            continue
        seen.add(c.key())
        deduped.append(c)
    return deduped


# ---------- Cache ----------


def _cache_path(book_id: str) -> Path:
    return get_settings().metadata_cache_dir / f"{book_id}.json"


def _load_cached(book_id: str) -> list[MetadataCandidate] | None:
    p = _cache_path(book_id)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if (time.time() - data.get("at", 0)) > CACHE_TTL_SECONDS:
            return None
        return [MetadataCandidate(**c) for c in data.get("candidates", [])]
    except Exception as e:
        log.warning("metadata cache read failed for %s: %s", book_id, e)
        return None


def _save_cached(book_id: str, candidates: list[MetadataCandidate]) -> None:
    p = _cache_path(book_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("w", encoding="utf-8") as f:
            json.dump(
                {"at": time.time(), "candidates": [asdict(c) for c in candidates]},
                f,
                ensure_ascii=False,
            )
    except Exception as e:
        log.warning("metadata cache write failed for %s: %s", book_id, e)


async def get_candidates_for(
    book_id: str, title: str, author: str | None, *, force_refresh: bool = False
) -> list[MetadataCandidate]:
    if not force_refresh:
        cached = _load_cached(book_id)
        if cached:
            return cached
    fresh = await find_candidates(title, author)
    _save_cached(book_id, fresh)
    return fresh
