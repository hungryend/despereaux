# despereaux

Self-hosted ebook server with predictive page caching. Plex-style library, in-browser reader for EPUB / PDF / CBZ / CBR / MOBI (auto-converted), per-user reading progress, and aggressive page prefetching so swipes are instant.

Designed to drop into a [linuxserver](https://linuxserver.io)-style media-server stack behind SWAG + Authentik forward-auth.

## Status

- **Phase 1 (MVP)** — EPUB library + reader, Authentik auth, Docker + SWAG ✓ shipped
- **Phase 2** — PDF / CBZ / CBR / MOBI conversion + external metadata enrichment
- **Phase 3** — Direction-aware prefetch queue (the headline feature)
- **Phase 4** — Bookmarks, full-text search, PWA offline mode

## Deploy (media-server stack)

Despereaux is wired into the existing [Sipador/media-server](https://github.com/Sipador/media-server) stack:

- The service block is appended to `media-server/docker-compose.yml` (after `audiobookshelf`)
- `media-server/swag-proxy-confs/despereaux.subdomain.conf` routes `despereaux.sipador.duckdns.org` to the container, gated by Authentik forward-auth
- `media-server/homepage-config/services.yaml` has a Homepage tile

On the deploy box (where the media-server stack runs):

```bash
# Clone despereaux as a sibling of media-server (the compose's `build: ../despereaux`)
cd /path/to/projects
git clone <despereaux-remote> despereaux

# Pull the media-server changes that wire despereaux in
cd media-server
git pull

# Copy the SWAG conf into the live SWAG config
cp swag-proxy-confs/despereaux.subdomain.conf ${MOUNT_POINT}/swag/config/nginx/proxy-confs/

# Copy the updated services.yaml (Homepage dashboard tile)
cp homepage-config/services.yaml ${MOUNT_POINT}/homepage/config/services.yaml

# Create the persistent data dir for despereaux
mkdir -p ${MOUNT_POINT}/despereaux

# Build + start despereaux, restart swag + homepage
docker compose up -d --build despereaux
docker compose restart swag homepage
```

In Authentik UI: create application `Despereaux`, launch URL `https://despereaux.sipador.duckdns.org`, bind to the existing SWAG forward-auth outpost. Create group `ebook-admin` and add yourself.

## Local development

```bash
uv sync
cd frontend && npm install && npm run build && cd ..
uv run uvicorn despereaux.main:app --reload --port 8000
```

`DESPEREAUX_DEV_MODE=true` (set in the local `.env`) bypasses Authentik with a `devuser` in the `ebook-admin` group. Books go in `data/ebooks/`; the SQLite DB and covers live in `data/config/`.

Generate a sample EPUB for testing:

```bash
uv run python scripts/make_sample_epub.py data/ebooks/sample.epub
```

Trigger an ingest scan:

```bash
curl -X POST http://127.0.0.1:8000/api/admin/scan
```

## Layout

```
despereaux/
├── Dockerfile                       # multi-stage: Vite/TS frontend + Python + Calibre
├── docker-compose.snippet.yml       # service block (already pasted into media-server compose)
├── swag/despereaux.subdomain.conf   # SWAG proxy conf (already copied to media-server)
├── src/despereaux/                  # FastAPI backend
└── frontend/                        # Vite + TypeScript reader (epub.js)
```

See the full plan at `~/.claude/plans/i-want-to-create-hazy-spring.md`.
