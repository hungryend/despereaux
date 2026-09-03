# Changelog

All notable changes to despereaux are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Regression suite** ahead of the full dependency refresh, in three tiers.
  In-repo: ~70 new tests covering the previously untested read path
  (`/api/books/{id}/file` ETag/304/Range/content-negotiation, covers,
  manifest, download logging), books search/filter/pagination, the progress
  API (incl. per-user isolation), libraries, EPUB/PDF ingest end-to-end (the
  pypdfium2 cover render finally has coverage), the scanner, cover generation,
  bcrypt/session primitives — plus the first-ever automated run of the real
  alembic migration chain (upgrade-to-head, idempotency, and a
  model-vs-migration drift check with a documented allowlist of pre-existing
  benign index divergences). Black-box: an env-gated smoke tier
  (`tests/smoke`, enabled by `DESPEREAUX_SMOKE_URL`) that drives a *running*
  instance over real HTTP — auth bootstrap through native `/setup` + the
  default API key, Range/304 over a real socket, reader assets, watcher
  ingest, in-container Calibre MOBI→EPUB auto-convert, and in-container PDF
  metadata/cover extraction; point it at any deployment read-only with
  `DESPEREAUX_SMOKE_TOKEN` (no `DESPEREAUX_SMOKE_RW`). Browser: Playwright
  checks that epub.js actually paginates and PDF.js actually rasterises in
  headless Chromium (`DESPEREAUX_SMOKE_BROWSER=1`). CI gains a `smoke-test`
  job (compose up the freshly built image via new `docker-compose.ci.yml`,
  seed fixtures with `scripts/make_smoke_fixtures.py`, run the black-box +
  browser tiers), and **publishing to GHCR is now gated on it** — every
  published image has booted, migrated a fresh DB, scanned a library,
  converted a MOBI, and rendered an EPUB + PDF in a real browser.
- **Per-user default API key, revealable on demand**: every user automatically
  gets an "API key" on their Account page — masked until revealed, with Copy
  and Regenerate buttons — for pasting into client apps (Furlough). Unlike
  additional tokens (still stored hash-only, shown once), the default key is
  stored retrievably so it can be re-shown — the same trade-off Sonarr/Radarr/
  Plex make. Endpoints: `GET /api/tokens/default`, `POST /api/tokens/default/rotate`.
- **User menu in the header**: clicking your username opens Account / Users
  (admins) / **Sign out**. Sign out now works in both auth modes — in authentik
  mode it bounces to the outpost's per-app sign-out, which clears the proxy
  session (also the fix for "still signed in as the old user" after an
  Authentik impersonation).
- **"On deck" shelf on the library home**: books you're actively reading show in
  a continue-reading row at the top, most-recently-read first. Click a cover for
  a menu — **Resume reading**, **View details**, or **Mark as unread** (clears your
  saved position, so the book leaves the shelf but stays in the library). A book
  only opened to its first page (≈0%) stays in the library, off the shelf; the
  shelf spans all libraries and is hidden while searching. "Mark as unread" is
  also on the book's detail page. New route `POST /book/{id}/progress/clear`.
- **Sort & group the library**: a sort bar on the library home with **Title A–Z**
  (default), **By author**, and **Recently added**. "By author" splits the grid
  into per-author sections — a co-authored book appears under each of its
  authors, and authorless books fall under "Unknown author". The choice carries
  across search and library-tab navigation. New `?sort=` query param.

### Changed
- **All dependencies refreshed to latest** (July 2026), interpreter and
  toolchain included. Runtime image now runs **Python 3.13**
  (`python:3.13-slim`; frontend build stage `node:24-alpine`), and
  `.python-version` (3.13) is now committed so local dev, CI, and the image
  share one interpreter. Python packages: fastapi 0.139, uvicorn 0.49,
  **structlog 26**, alembic 1.18.5, Pillow 12.3, pypdfium2 5.11, plus
  pyproject floors raised to match the lock (bcrypt>=5.0, ebooklib>=0.20,
  lxml>=6.1, httpx>=0.28, watchfiles>=1.2, aiosqlite>=0.22, …). Frontend:
  **pdfjs-dist 4 → 6** (reader updated for the removed `canvasContext` render
  parameter and the loading-task-owned teardown), **Vite 6 → 8**
  (Rolldown-based), TypeScript 6; epubjs stays 0.3.93 (upstream latest). CI:
  `astral-sh/setup-uv@v8`, Node 24. Dependabot now opens grouped PRs for
  majors too (`python-major`/`npm-major` groups) instead of silently skipping
  them. `alembic.ini` gains `path_separator = os` (silences the alembic 1.16+
  deprecation). Verified by the full regression suite (148 in-repo tests on
  3.13) and the container smoke + browser tier in CI.

### Fixed
- **Scanned PDFs no longer open as blank white pages.** Any book whose pages
  are JPEG 2000 images (`/JPXDecode` — the output of most book scanners, and
  what every scanned codex in the library uses) rendered as an empty page: the
  page count, outline and even the invisible OCR text layer were all there, but
  nothing was drawn. PDF.js 5 decodes JPX in a WebAssembly OpenJPEG module that
  it fetches at runtime from the URL given by the `wasmUrl` API parameter —
  which the reader never set, so the decoder failed to initialise. It is a
  *warning*, not an error, so the reader's error overlay stayed silent and the
  page just looked empty. Same story for JBIG2 (the other scanner format),
  character maps, the standard-14 font data and ICC profiles: all four are
  package data PDF.js loads by URL rather than importing, so Vite never saw
  them and none were in the bundle. They are now emitted by a copy plugin in
  `vite.config.ts` into a **version-scoped** `assets/pdfjs-<version>/`
  directory — these files must keep their exact names (PDF.js concatenates
  `${wasmUrl}openjpeg.wasm`), so a content hash is impossible and the version
  in the path is what stops an `immutable`-cached copy from ever meeting a
  mismatched reader build, the same trap as the stale worker in #63. The reader
  passes all four URLs to `getDocument()`.
  Two regression nets, because nothing existing could have caught this: a new
  `sample-jpx.pdf` fixture (one JPEG 2000 page — the shape a scanned book has;
  `sample.pdf` is three *blank* vector pages, so "the reader drew nothing" was
  indistinguishable from success), and a browser check that asserts **opaque,
  non-white pixels** actually landed on the canvas and that PDF.js logged no
  decode failure. The asset smoke test also fetches `openjpeg.wasm` and one
  file from each data directory. Verified both ways: the new check fails on the
  old code with the exact console warning, and passes on the fix.
- **Unblocked the GHCR publish gate by dropping pip from the runtime image.**
  Trivy parses `pip/_vendor/vendor.txt` as an installed-package list, so pip's
  vendored pins (`msgpack==1.1.2`, `setuptools==70.3.0`) were reported as
  fixable HIGH findings and blocked publishing — even though neither is an app
  dependency, no app code imports either, and the image's only real msgpack is
  Debian's 1.0.3 for Calibre. pip exists purely to bootstrap uv (uv resolves and
  installs standalone; the runtime CMD runs uvicorn from `/app/.venv`), so it is
  now removed in the same layer once uv is installed. That deletes the vendored
  code rather than suppressing the advisory, and closes the whole class of
  failure — every pip upgrade re-pins its vendored set, so any future advisory
  against one of those pins would have blocked the gate again. Verified by
  building the image and running the gate's exact Trivy invocation (clean), plus
  a boot check: `/healthz` ok, library renders, Calibre 8.5.0 still converts.
- **Finished books now leave the "On deck" shelf.** The shelf filtered progress
  with a lower bound only (`> 1%`), so a book read cover-to-cover sat at 100%
  and stayed pinned to the continue-reading row forever, with no way to clear
  it short of "Mark as unread" (which throws the reading position away).
  Progress at or above 99% now counts as read and drops off the shelf. The
  threshold isn't exactly 100% because epub.js' CFI percentage can stop a hair
  short on the final page; the paged PDF/comic readers already report an exact
  `page/total` of 1.0. The EPUB reader now also pins the end of the book to
  100% outright, using epub.js' `atEnd` (last page of the last spine item), so
  "read to the last page" is deterministic rather than a rounding accident.
  A new **Mark as read** action (deck menu + book detail page) does the same
  thing by hand for a book finished elsewhere or abandoned near the end — it
  pins progress to 100% but *keeps* the saved position, so it stays re-readable
  (unlike "Mark as unread", which deletes it). The detail page now reads
  "Finished" instead of a stale "Last read: 100%", and reports whole
  percentages ("42%", not "42.0%").
- **The On-deck shelf's "most-recently-read first" order actually works now.**
  `reading_progress.updated_at` never moved after the row was first written:
  the save path is an upsert, and a column's SQLAlchemy `onupdate` doesn't fire
  for the `DO UPDATE` clause, so the shelf was ordering by *first-opened* time.
  The timestamp is now set explicitly on every save.
- **PDF reading restored on older tablets and Android WebViews**: the pdf.js
  4→6 upgrade silently raised the minimum browser to Chromium 125+ — *even in
  its legacy bundle* (mozilla/pdf.js#21152) — so on older devices the reader
  died before ever requesting the PDF (blank page, no errors server-side).
  The reader now pins **pdf.js v5.7 and ships its legacy build** (floor:
  Chrome 110, Feb 2023) — same API, same worker filename — and a dependabot
  ignore rule stops the v6 major from returning via the npm-major group until
  the device fleet clears Chromium 125. The Playwright PDF check now also
  turns pages (same-canvas re-render, the pattern majors break) and asserts
  relative page movement so the saved-position restore can't skew it.
- **Page turns are now the full sides of the screen**: prev/next are
  full-height panels down the left and right edges, from under the reader bar
  to the bottom of the viewport — the whole side of the screen is the button,
  not a control floating in the middle of it, so the thumb turns a page
  wherever it happens to be holding the tablet or phone. They are still real `<button>`s (keyboard, screen readers), and on
  touch they behave like the content underneath: a tap turns the page, a
  horizontal swipe turns it in the swipe's direction, and a drag while a PDF
  or comic is zoomed in pans the page instead of turning it. The EPUB text
  column is inset between the zones so no word ever sits under one and text
  stays selectable; PDF and comic pages keep the full viewport width with the
  zones floating over their outer edges. This supersedes both earlier attempts
  at the problem — the side gutters that narrowed the column, then the
  reserved top/bottom strip — and the **⇅ strip toggle is removed** along with
  its `despereaux:navPos` preference, since there is no longer a strip to
  move. Also gone: EPUB's click-the-inner-20%-of-the-column page turn, which
  used to fire on a tap meant for the text. Furlough inherits all of it (same
  reader page) and needs no app-side change: its bottom-center Listen pill no
  longer has buttons to collide with. Guarded by rewritten Playwright
  regression tests — zones flush to both edges, spanning bar-to-bottom, never
  overlapping the text, at phone and tablet widths.
- **`app.css` is now cache-busted** with a
  content-hash `?v=` (the reader bundle already did this), so a new release no
  longer leaves browsers on a heuristically-fresh *old* stylesheet — which is
  what made the new On-deck shelf render unstyled (over-large covers, mislaid
  progress bar) until a hard refresh. Exposed via a `static_version()` Jinja
  global; applied to the three templates that link `app.css` (base/login/setup).
  Also centers the On-deck no-cover placeholder glyph.
- **Dependencies refreshed again to latest** (August 2026) alongside that
  change: frontend on **TypeScript 7** and Vite 8.2.2 (`@xmldom/xmldom`
  override 0.9.12); Python lock upgraded across the board — fastapi 0.141.1,
  starlette 1.6.0, uvicorn 0.52.4, sqlalchemy 2.0.52, pydantic-settings 2.15,
  pypdf 6.16.2, pypdfium2 5.13, rarfile 4.5, ruff 0.16.4, playwright 1.62;
  CI actions on `astral-sh/setup-uv@v10.0.1` and `actions/setup-node@v7`.
  **pdfjs-dist deliberately stays on v5** (legacy build, Chrome 110 floor) —
  the dependabot ignore rule and the reason behind it are unchanged.

## [0.4.0] — 2026-06-11

### Added
- **Native authentication mode** (`DESPEREAUX_AUTH_MODE=native`): despereaux's
  own login page with bcrypt passwords and signed session cookies — no reverse
  proxy or identity provider required. First run redirects to `/setup` to
  create the admin account. In native mode the `X-authentik-*` headers are
  ignored (they are only trustworthy behind a forward-auth proxy). The default
  mode remains `authentik`; existing deployments are unchanged.
- **Admin user management UI** at `/admin/users` (both modes): create users
  (with a password in native mode, or pre-provisioned/token-only), reset
  passwords, grant/revoke admin. Admins can't demote themselves.
- **Account page** at `/account` (both modes): every signed-in user can mint
  their own API tokens (shown once, with copy button) for native apps like
  Furlough, see last-used times, and revoke them. Backed by new self-service
  endpoints `GET/POST /api/tokens` + `DELETE /api/tokens/{id}` (own tokens
  only). Native mode adds self-service password change.

## [0.3.0] — 2026-06-10

### Added
- Per-user, revocable API tokens for native clients (Furlough): admin
  mint/list/revoke under `/api/admin/tokens`, accepted via
  `Authorization: Bearer <token>` or the `despereaux_token` cookie (the cookie
  exists for WebView subresource auth). Tokens are stored as SHA-256 hashes and
  the plaintext is shown once at creation. New `GET /api/me` identity probe.
  An explicitly presented invalid token is rejected with 401 even in dev mode.

### Fixed
- Release workflow: GitHub Release creation on tag push needs `contents: write` permission on GITHUB_TOKEN; the v0.1.0 tag publication failed on this. The image itself was published to GHCR successfully.

### Security
- Base image packages are now `apt-get upgrade`d at build time — the slim base
  lags Debian point releases, and newly disclosed fixable CVEs (poppler, OpenSSL)
  were blocking the publish gate.
- Removed the legacy `release.yml` workflow: it pushed `latest` to GHCR on every
  main push **without** the Trivy gate, silently bypassing the gated publish job
  in `ci.yml` (which tags, cuts the release, and only pushes after a clean scan).

## [0.1.0] — 2026-06-03

### Added
- In-browser EPUB reader (epub.js) with table of contents, page-turn navigation
  (arrows / space / swipe), and dark single-page layout with a max-width column.
- In-browser PDF reader (PDF.js) with HTTP range streaming so large files open
  before the whole download completes.
- MOBI / AZW / AZW3 ingest via Calibre's `ebook-convert` — converted EPUB is
  cached by content hash; `/download` continues to serve the original.
- Multi-library support via `DESPEREAUX_LIBRARIES` JSON env var; UI shows one
  tab per library, hidden when only a single library is configured.
- Library scanner: one-shot `POST /api/admin/scan` plus a live `watchfiles`
  watcher that picks up new/changed/deleted files within seconds.
- External metadata enrichment from Google Books + Open Library with fuzzy
  match scoring (rapidfuzz). Auto-applies high-confidence matches at ingest
  and exposes a manual picker UI with free-form keyword search override.
- Parent / asset book relationship — attach maps, handouts, supplements under
  a main book so they don't clutter the library grid.
- Duplicate detection — surfaces books that share content hash, ISBN, or
  external metadata ID with a one-click Remove action.
- Per-user reading progress, debounced + flushed on `beforeunload` /
  `pagehide` / `visibilitychange` via keep-alive `fetch`.
- Webhook ingest endpoint `POST /api/admin/sync` with bearer-token auth for
  Readarr-style "Custom Script" integrations.
- Reverse-proxy / forward-auth integration via `X-authentik-*` headers
  (compatible with Authentik, Authelia, Keycloak, and any provider that
  injects those header names).
- Multi-stage Docker image published to GHCR with SBOM + build provenance.
- CI: ruff lint + format, pytest, pip-audit, npm audit, Trivy scan with SARIF
  upload to GitHub Code Scanning.
- Release workflow: pushes `:latest` + `:sha-<short>` on every main commit,
  `:vX.Y.Z` on semver tags. Includes a re-scan of the published image.
- Dependabot configured for Python (uv), npm, Docker base images, and
  GitHub Actions.

[Unreleased]: https://github.com/hungryend/despereaux/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hungryend/despereaux/releases/tag/v0.1.0
