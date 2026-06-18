<h1 align="center">despereaux</h1>

<p align="center">
  <img src="src/despereaux/static/img/logo.svg" alt="despereaux logo" width="200" />
</p>

<p align="center">
  Self-hosted ebook server. Plex-style library + in-browser reader for EPUB / PDF / MOBI / AZW.
  <br/>
  <em>Per-user reading progress, multi-format support, external metadata enrichment, predictive page caching.</em>
</p>

<p align="center">
  <a href="https://github.com/hungryend/despereaux/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/hungryend/despereaux/actions/workflows/ci.yml/badge.svg"/></a>
  <a href="https://github.com/hungryend/despereaux/releases"><img alt="Release" src="https://img.shields.io/github/v/release/hungryend/despereaux"/></a>
  <a href="https://github.com/hungryend/despereaux/pkgs/container/despereaux"><img alt="GHCR" src="https://img.shields.io/badge/ghcr.io-hungryend%2Fdespereaux-blue?logo=docker"/></a>
</p>

---

## Quick start

```bash
docker run -d \
  --name despereaux \
  -p 8810:8000 \
  -v /path/to/your/ebooks:/ebooks:ro \
  -v despereaux_config:/config \
  ghcr.io/hungryend/despereaux:latest
```

Open <http://localhost:8810> — that's it. Dev mode is on by default, a placeholder user is auto-created, and the library scan runs automatically. Drop more ebooks into the host directory and the watcher picks them up within a few seconds.

### What you get

- **In-browser reader** — page-turn navigation, position retention per book, table of contents, mobile-friendly swipe + keyboard
- **Format support**:
  - **EPUB** — native via [epub.js]
  - **PDF** — native via [PDF.js], with byte-range streaming so huge files open instantly
  - **MOBI / AZW / AZW3** — auto-converted to EPUB at ingest using [Calibre] (bundled in the image)
  - **CBZ / CBR** — native page-image comic reader; archives are read on demand (CBR via the `unar` binary baked into the image), one page served at a time
- **Convert to EPUB** — a per-book button turns PDF / MOBI / AZW into a clean, reflowable EPUB with an auto-linked table of contents. PDFs go through a Markdown intermediate ([PyMuPDF]-based `pdf2md`) for solid headings + inline images; scanned/image PDFs are detected and left as the original PDF — or OCR'd into a reflowable EPUB via the optional [`ocr` sidecar](#scanned-pdf-ocr-optional). Conversions run in the background as tracked jobs (status survives a restart) and surface in a header notifications menu when done.
- **Read-aloud bridge** — EPUB and PDF readers expose a WebView JS bridge (`window.__furloughTts`) so native clients (e.g. the Furlough Android reader) can read a book aloud with sentence/word highlighting and auto page-turns.
- **Live library indexing** — filesystem watcher catches new/changed/deleted files within seconds, plus a one-shot scan endpoint
- **External metadata enrichment** — automatic Google Books + Open Library lookup at ingest, plus a manual picker UI with keyword search for tricky matches
- **Multi-library support** — group your collection into named libraries (Fiction / Comics / Reference / etc.)
- **Parent / asset books** — attach maps, handouts, supplements under a main book so they don't clutter the library grid
- **Duplicate detection** — surfaces other books that share content hash, ISBN, or external ID
- **Webhook for external import pipelines** — bearer-token-authed `/api/admin/sync` endpoint for tools like Readarr-style "Custom Scripts"

---

## docker-compose

```yaml
services:
  despereaux:
    image: ghcr.io/hungryend/despereaux:latest
    container_name: despereaux
    restart: unless-stopped
    ports:
      - "8810:8000"
    volumes:
      - /path/to/your/ebooks:/ebooks:ro
      - despereaux_config:/config
    environment:
      - TZ=UTC
      - PUID=1000
      - PGID=1000
      # See "Configuration" below for the full list.
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

volumes:
  despereaux_config:
```

---

## Configuration

All options are environment variables on the container. **None are required** — the defaults give you a working single-library deployment in dev mode.

### Core

| Variable | Default | Description |
|---|---|---|
| `DESPEREAUX_LIBRARY_PATH` | `/ebooks` | Path **inside the container** where books live. Match this to the right-hand side of your library volume mount. |
| `DESPEREAUX_DATA_DIR` | `/config` | Persistent state directory (SQLite DB, covers, caches). Mount a named volume here. |
| `DESPEREAUX_DB_URL` | `sqlite+aiosqlite:////config/despereaux.db` | SQLAlchemy connection string. Override if you'd rather use PostgreSQL: `postgresql+asyncpg://user:pass@host/db`. |
| `DESPEREAUX_LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

### Authentication

despereaux supports two auth modes, selected with `DESPEREAUX_AUTH_MODE`:

- **`native`** — despereaux's own login page. Passwords are bcrypt-hashed and
  sessions ride a signed HTTP-only cookie. On first run you're redirected to
  `/setup` to create the admin account; after that, manage users at
  `/admin/users`. Pick this if you don't run an identity provider.
- **`authentik`** (default) — identity comes from `X-authentik-*` headers
  injected by a forward-auth reverse proxy (Authentik, Authelia, Keycloak…).
  despereaux has no login page; the proxy is the gate (see "Reverse-proxy
  authentication" below). In this mode despereaux must only be reachable
  through the proxy — the headers are trusted.

In **both** modes every signed-in user can mint **per-user API tokens** on the
`/account` page (shown once, copy-paste into a client app). Clients send
`Authorization: Bearer <token>` — or the `despereaux_token` cookie for WebView
embedding — and authenticate as the owning user. In native mode the
`X-authentik-*` headers are ignored entirely, since without a trusted proxy
they would be client-controlled.

| Variable | Default | Description |
|---|---|---|
| `DESPEREAUX_AUTH_MODE` | `authentik` | `authentik` (forward-auth headers) or `native` (built-in login page + user management). |
| `DESPEREAUX_SESSION_SECRET` | *(generated)* | Secret signing native-mode session cookies. If unset, one is generated and persisted at `{DATA_DIR}/session-secret`. |
| `DESPEREAUX_DEV_MODE` | `true` | When `true`, a placeholder `devuser` is auto-created and auth is bypassed for credential-less requests. Set `false` in any real deployment. |
| `DESPEREAUX_ADMIN_GROUP` | `ebook-admin` | Authentik-mode: members of this group (from `X-authentik-groups`) are admins. Native-mode admins are flagged per-user in `/admin/users` instead. |

### Multiple libraries (optional)

If you want to split your collection into multiple named libraries that show as tabs in the UI, set `DESPEREAUX_LIBRARIES` to a JSON array:

```bash
DESPEREAUX_LIBRARIES='[{"name":"Fiction","path":"/libraries/fiction"},{"name":"Comics","path":"/libraries/comics"}]'
```

Each library is a `{name, path}` object. The `path` is an absolute path inside the container; mount each host directory to its matching container path. Example with three libraries:

```yaml
services:
  despereaux:
    image: ghcr.io/hungryend/despereaux:latest
    volumes:
      - /host/path/fiction:/libraries/fiction:ro
      - /host/path/comics:/libraries/comics:ro
      - /host/path/manuals:/libraries/manuals:ro
      - despereaux_config:/config
    environment:
      - DESPEREAUX_LIBRARIES=[{"name":"Fiction","path":"/libraries/fiction"},{"name":"Comics","path":"/libraries/comics"},{"name":"Manuals","path":"/libraries/manuals"}]
```

If `DESPEREAUX_LIBRARIES` is unset, the container falls back to a single library named `Default` at `DESPEREAUX_LIBRARY_PATH`. The UI hides the library tabs when only one library exists.

### External metadata enrichment

| Variable | Default | Description |
|---|---|---|
| `DESPEREAUX_GOOGLE_BOOKS_API_KEY` | _unset_ | Optional Google Books API key. Without one, requests are still made but subject to lower per-IP rate limits. Open Library fills any gaps. |

### Webhook for external import tools (optional)

| Variable | Default | Description |
|---|---|---|
| `DESPEREAUX_WEBHOOK_TOKEN` | _unset_ | Shared secret for the `/api/admin/sync` webhook. Until set, the endpoint returns `503 Service Unavailable`. |

Generate a token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Once set, any external tool can trigger an ingest:

```bash
# Targeted: ingest specific paths (paths are container-side, i.e. inside the despereaux container)
curl -X POST http://despereaux:8000/api/admin/sync \
  -H "Authorization: Bearer $DESPEREAUX_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"paths":["/ebooks/path/to/new-book.epub"]}'

# Full library rescan: empty paths
curl -X POST http://despereaux:8000/api/admin/sync \
  -H "Authorization: Bearer $DESPEREAUX_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"paths":[]}'
```

A sample shell script for Readarr-style "Custom Scripts" callers is included at [`scripts/notify-despereaux.sh`](scripts/notify-despereaux.sh).

### Scanned-PDF OCR (optional)

Born-digital PDFs and MOBI/AZW convert out of the box. **Scanned / image PDFs** (page images with no
real text) are skipped by default — read the original PDF. To convert those too, run the optional
**`ocr` sidecar** (Ollama + a vision model) and point despereaux at it:

| Variable | Default | Description |
|---|---|---|
| `DESPEREAUX_OLLAMA_HOST` | _unset_ | Ollama base URL. Empty ⇒ OCR off. Set to `http://ocr:11434` (the sidecar) or any reachable Ollama. |
| `DESPEREAUX_OLLAMA_MODEL` | `deepseek-ocr:3b` | Vision model tag (pulled by the sidecar). Alternatives: `qwen2.5vl`, `minicpm-v`, `granite3.2-vision`. |
| `DESPEREAUX_OLLAMA_OCR_TIMEOUT` | `600` | Per-page OCR timeout (seconds). |
| `DESPEREAUX_OLLAMA_DPI` | `150` | Page render resolution for OCR. |
| `DESPEREAUX_OLLAMA_NUM_CTX` | `8192` | Model context window. |

```bash
echo "DESPEREAUX_OLLAMA_HOST=http://ocr:11434" >> .env
docker compose --profile ocr up -d        # starts despereaux + the ocr sidecar
```

The sidecar pulls the model (~6.7 GB, needs Ollama ≥ 0.13) once into the `ollama-models` volume.
Clicking **Convert to EPUB** on a scanned PDF then runs OCR in the background — the conversions menu
shows `OCR page N/M` and your browser is notified when it finishes. OCR is **slow on CPU** (the
sidecar is CPU-only — minutes per book); for speed, skip the sidecar and point `DESPEREAUX_OLLAMA_HOST`
at a GPU Ollama (NVIDIA, or AMD ROCm) with `OLLAMA_KEEP_ALIVE` set high so the model stays resident.

### tomeforge conversion sidecar (optional)

PDF→EPUB normally runs **in-process** and needs the AGPL `pdf` extra (PyMuPDF) baked into the image.
Alternatively, offload it to a [tomeforge](https://github.com/hungryend/tomeforge) **conversion
sidecar** — then despereaux can be built **without** the `pdf` extra (MIT-only core) and still convert
PDFs. The sidecar does PDF → Markdown → EPUB (and scanned-PDF OCR, reaching the same Ollama); despereaux
still applies its own linked-TOC guarantee and scanned backstop to the result.

| Variable | Default | Description |
|---|---|---|
| `DESPEREAUX_TOMEFORGE_HOST` | _unset_ | tomeforge service base URL. Empty ⇒ PDF→EPUB runs in-process. Set to `http://tomeforge:8400` (the sidecar) or any reachable tomeforge. |
| `DESPEREAUX_TOMEFORGE_TIMEOUT` | `1800` | Overall per-conversion ceiling (seconds). |

```bash
echo "DESPEREAUX_TOMEFORGE_HOST=http://tomeforge:8400" >> .env
docker compose --profile tomeforge up -d            # despereaux + the tomeforge sidecar
docker compose --profile tomeforge --profile ocr up -d   # …also OCR scanned PDFs
```

Resolution order for the PDF path: **sidecar** (if `DESPEREAUX_TOMEFORGE_HOST` is set) → **in-process**
pdf2md (if the `pdf` extra is installed) → otherwise the Convert button reports that neither is
available. MOBI/AZW always convert locally via Calibre, sidecar or not.

### File ownership

| Variable | Default | Description |
|---|---|---|
| `PUID` | `1000` | UID for files created by the container in bind mounts. Set to your host user's UID (`id -u`). |
| `PGID` | `1000` | GID for files created by the container. |
| `TZ` | `UTC` | Container timezone. Affects timestamps in logs and the database. |

---

## Persistent state

The container writes everything that needs to survive restarts to `/config`. Mount a named Docker volume or a bind mount on this path.

| Path | What's there |
|---|---|
| `/config/despereaux.db` | SQLite database — books, users, reading progress, bookmarks |
| `/config/covers/` | 600px WebP cover thumbnails |
| `/config/converted/` | MOBI/AZW books auto-converted to EPUB at ingest (cached by content hash) |
| `/config/exports/` | On-demand "Convert to EPUB" outputs (cached by content hash) |
| `/config/cache/` | Future: server-side page cache for predictive prefetch |
| `/config/metadata-cache/` | 30-day TTL cache of external metadata lookups |

Back this directory up regularly if you care about your reading positions and library metadata.

---

## Reverse-proxy authentication

For multi-user public deployments, set `DESPEREAUX_DEV_MODE=false` and put the container behind a reverse proxy (Caddy, nginx, SWAG, Traefik) that injects identity headers from an upstream identity provider (Authentik, Authelia, Keycloak, etc.).

Required headers on each authenticated request:

| Header | Required | Notes |
|---|---|---|
| `X-authentik-username` | yes | Used as the unique user identity |
| `X-authentik-email` | no | Stored on the user record |
| `X-authentik-groups` | no | Comma-separated. Membership in `DESPEREAUX_ADMIN_GROUP` gates `/api/admin/*` endpoints |

Without these headers, the container returns `401 Unauthorized`. The header names are Authentik-flavoured, but any forward-auth integration can supply them — they're just three HTTP headers, the upstream identity provider doesn't matter.

---

## API surface

The web UI uses the same JSON API. All endpoints require auth (either dev-mode or forward-auth headers) except `/healthz` and the static files.

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe (always 200) |
| `GET` | `/api/books` | List books. Query: `library=`, `search=`, `limit=`, `offset=` |
| `GET` | `/api/books/{id}` | Book detail |
| `GET` | `/api/books/{id}/file` | Stream the book file (range requests supported) |
| `GET` | `/api/books/{id}/cover` | Serve the cover thumbnail |
| `GET` | `/api/books/{id}/download` | Download the original file (audit-logged) |
| `GET` | `/api/books/{id}/manifest` | Quick boot metadata for the reader |
| `GET` | `/api/books/{id}/page/{n}` | Serve the nth page image (0-based) of a CBZ/CBR comic, extracted on demand |
| `GET` / `PUT` | `/api/books/{id}/progress` | Get / save reading position for the current user |
| `GET` | `/api/progress` | Bulk reading progress for the current user across all books |
| `GET` | `/api/libraries` | List configured libraries with book counts |
| `GET` | `/api/books/{id}/metadata-candidates` | List external metadata candidates for the book |
| `POST` | `/api/books/{id}/select-metadata-match` | Apply a candidate's metadata |
| `POST` | `/api/books/{id}/convert` | Start a background "convert to EPUB" job |
| `GET` | `/api/books/{id}/convert/status` | Poll conversion job status / phase |
| `GET` | `/api/books/{id}/convert/download` | Download the converted EPUB |
| `GET` | `/api/conversions` | List the current user's conversion jobs (notifications menu) |
| `POST` | `/api/conversions/clear` | Clear finished conversion notifications |
| `POST` | `/api/admin/scan[?library=...]` | Trigger a library scan (admin-only) |
| `POST` | `/api/admin/sync` | Webhook ingest (token auth) |

OpenAPI docs at `/api/docs` once the container is running.

---

## Build from source

```bash
git clone https://github.com/hungryend/despereaux.git
cd despereaux
docker compose up -d --build
```

The repo's `docker-compose.yml` builds a fresh image from the local source. Frontend (Vite + TypeScript) and backend (FastAPI + Calibre) are both built inside the multi-stage Dockerfile — no host tooling required beyond Docker.

### Local development without Docker

```bash
uv sync --extra formats --extra pdf   # Python deps (`pdf` = PyMuPDF, AGPL — for PDF→EPUB)
cd frontend && npm install && npm run build && cd ..   # frontend bundle
uv run alembic upgrade head       # initialise the DB
uv run uvicorn despereaux.main:app --reload --port 8000
```

Requires Python 3.12+, Node 22+, and Calibre on the host PATH for conversions (PDF / MOBI / AZW).

---

## CI/CD

- **CI** (`.github/workflows/ci.yml`) runs on every PR + push: ruff lint + format, pytest with coverage, pip-audit, npm audit, Docker build, Trivy scan → uploaded to GitHub Code Scanning.
- **Publish** (the `Publish image (GHCR)` job in `.github/workflows/ci.yml`) on every push to `main`: builds a multi-stage image and publishes to `ghcr.io/hungryend/despereaux:latest` plus `:sha-<short>`. SemVer tags (`v1.2.3`) also publish `:v1.2.3`, scan the published image, and cut a GitHub Release.
- **Dependabot** opens weekly PRs for outdated Python, npm, Docker base image, and GitHub Actions versions.

All images include SBOM + build provenance attestations.

---

## Roadmap

- Direction-aware predictive page caching (instant page turns)
- PWA support for full offline reading
- In-book full-text search
- Bookmarks UI

---

## License

MIT — see [LICENSE](LICENSE).

The optional `pdf` extra installs **[PyMuPDF]** (AGPL-3.0, or a commercial licence from Artifex) to power the PDF → Markdown → EPUB conversion (vendored as `src/despereaux/services/pdf2md.py`). It's enabled in the Docker image by default; build without `--extra pdf` for an AGPL-free install — PDF→EPUB is then unavailable, while MOBI/AZW conversion still works. All other dependencies are permissively licensed.

The mascot illustration is from [clker.com](https://www.clker.com/) (public domain clipart).

[epub.js]: https://github.com/futurepress/epub.js
[PDF.js]: https://github.com/mozilla/pdf.js
[Calibre]: https://calibre-ebook.com/
[PyMuPDF]: https://pymupdf.readthedocs.io/
