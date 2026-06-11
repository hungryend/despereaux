# Changelog

All notable changes to despereaux are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
