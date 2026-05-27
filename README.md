# despereaux

Self-hosted ebook server with predictive page caching. Plex-style library, in-browser reader for EPUB / PDF / CBZ / CBR / MOBI (auto-converted), per-user reading progress, and aggressive page prefetching so swipes are instant.

Designed to drop into a [linuxserver](https://linuxserver.io)-style media-server stack behind SWAG + Authentik forward-auth.

## Status

- **Phase 1 (MVP)** — EPUB library + reader, Authentik auth, Docker + SWAG ✓ shipped
- **Phase 2** — PDF / CBZ / CBR / MOBI conversion + external metadata enrichment
- **Phase 3** — Direction-aware prefetch queue (the headline feature)
- **Phase 4** — Bookmarks, full-text search, PWA offline mode

## Deploy (media-server stack)

Despereaux wires into the existing [Sipador/media-server](https://github.com/Sipador/media-server) stack. Three things have been changed in the media-server checkout:

1. **`media-server/docker-compose.yml`** — `despereaux` service block appended after `audiobookshelf`. This file IS git-tracked; commit + push when ready.
2. **`media-server/swag-proxy-confs/despereaux.subdomain.conf`** — routes `despereaux.sipador.duckdns.org`, Authentik-gated. **Not git-tracked** in media-server (the whole `swag-proxy-confs/` folder is untracked) — sync to the deploy box separately.
3. **`media-server/homepage-config/services.yaml`** — Homepage tile entry. Also **not git-tracked**; sync separately.

### On the deploy box

```bash
# 1. Clone despereaux as a sibling of media-server (compose's `build: ../despereaux`)
cd /path/to/projects
git clone <despereaux-remote> despereaux

# 2. Pull the media-server compose change
cd media-server
git pull

# 3. Drop the SWAG + Homepage configs into the live mount paths.
#    (These files live only on your dev box — copy them up via scp/rsync first
#    OR maintain them directly on the deploy box, your call.)
cp /path/to/despereaux.subdomain.conf ${MOUNT_POINT}/swag/config/nginx/proxy-confs/
#    Append the despereaux tile to your live services.yaml (see homepage-config/services.yaml for the snippet)

# 4. Create the persistent data dir for despereaux
mkdir -p ${MOUNT_POINT}/despereaux

# 5. Build + start despereaux, reload swag + homepage
docker compose up -d --build despereaux
docker compose restart swag homepage
```

### Authentik

Create application `Despereaux`, launch URL `https://despereaux.sipador.duckdns.org`, bind to the existing SWAG forward-auth outpost. Create group `ebook-admin` and add yourself.

### First scan

Once `despereaux.sipador.duckdns.org` resolves and you can log in, trigger the initial library scan:

```bash
curl -X POST https://despereaux.sipador.duckdns.org/api/admin/scan \
  -H "Cookie: <your-authentik-session-cookie>"
```

Or hit it from a browser via DevTools' fetch console while logged in.

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
