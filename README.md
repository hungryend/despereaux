# despereaux

[![CI](https://github.com/Sipador/despereaux/actions/workflows/ci.yml/badge.svg)](https://github.com/Sipador/despereaux/actions/workflows/ci.yml)
[![Release](https://github.com/Sipador/despereaux/actions/workflows/release.yml/badge.svg)](https://github.com/Sipador/despereaux/actions/workflows/release.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-despereaux-blue?logo=docker)](https://github.com/Sipador/despereaux/pkgs/container/despereaux)

Self-hosted ebook server with predictive page caching. Plex-style library, in-browser reader for EPUB / PDF / CBZ / CBR / MOBI (auto-converted), per-user reading progress, and aggressive page prefetching so swipes are instant.

Designed to drop into a [linuxserver](https://linuxserver.io)-style media-server stack behind SWAG + Authentik forward-auth.

## Status

- **Phase 1 (MVP)** — EPUB library + reader, Authentik auth, Docker + SWAG ✓ shipped
- **Phase 2** — PDF / CBZ / CBR / MOBI conversion + external metadata enrichment
- **Phase 3** — Direction-aware prefetch queue (the headline feature)
- **Phase 4** — Bookmarks, full-text search, PWA offline mode

## GitHub setup (one-time)

```bash
# In the despereaux/ directory, create the remote repo and push.
gh repo create Sipador/despereaux --public --source=. --remote=origin --push
# or, if creating the repo via the web UI:
git remote add origin git@github.com:Sipador/despereaux.git
git push -u origin main
```

After the first push, GitHub Actions will run automatically:

- **CI** (`.github/workflows/ci.yml`) — on every PR + push to `main`. Runs ruff lint + format, pytest (with coverage upload), `npm run build` for the frontend, builds the Docker image, scans it with **Trivy** (CRITICAL + HIGH severity, fixable only), and uploads the SARIF to GitHub **Code Scanning** so findings show up in the Security tab.
- **Release** (`.github/workflows/release.yml`) — on push to `main`, builds and pushes the image to **`ghcr.io/sipador/despereaux:latest`** plus `:sha-<short>`. On a `v*.*.*` git tag, also publishes `:v0.1.0`, `:0.1`, scans the *published* image, and cuts a GitHub Release.
- **Dependabot** (`.github/dependabot.yml`) — opens weekly PRs for outdated Python (uv), npm, Docker base image, and GitHub Actions versions. Patch + minor bumps are grouped; majors get their own PRs.

Pin to a specific version on the deploy box:

```yaml
# media-server/docker-compose.yml (already configured to pull from GHCR by default)
image: ghcr.io/sipador/despereaux:v0.1.0   # or :latest
```

### CVE response loop

1. Trivy + pip-audit + Dependabot all surface vulnerabilities (Code Scanning, PRs, Security tab).
2. Merge the Dependabot PR (CI revalidates the image).
3. Release workflow pushes a new `:latest`.
4. On the deploy box: `docker compose pull despereaux && docker compose up -d despereaux`.
   (Or add [Watchtower](https://containrrr.dev/watchtower/) to the stack for automatic rolls — keep it scoped to despereaux only with `WATCHTOWER_LABEL_ENABLE=true` so it doesn't touch Plex/Immich.)

## Run with Docker (standalone)

```bash
# 1. Point at your real ebook collection (or leave the default ./data/ebooks).
#    Open docker-compose.yml and edit the LEFT side of:
#      - ./data/ebooks:/ebooks
#    Example: change to `- /mnt/nas/Ebooks:/ebooks` or `- D:/Books:/ebooks`.

# 2. (Optional) Set a webhook token + dev-mode in a .env file beside compose.yml:
cp .env.compose.example .env
# Edit .env — generate a token if you want chaptarr integration.

# 3. Build + run.
docker compose up -d --build

# 4. Open http://localhost:8810  (or the host port you set in compose).
#    With DESPEREAUX_DEV_MODE=true a `devuser` is auto-created in the ebook-admin
#    group — no Authentik required.

# 5. Trigger a one-shot scan of the library (it also runs automatically at
#    startup, and the filesystem watcher picks up new files thereafter).
curl -X POST http://localhost:8810/api/admin/scan
```

Mounts (full docs inline in `docker-compose.yml`):

| Host path (left) | Container path (right) | What's there |
|---|---|---|
| `./data/ebooks` (default) | `/ebooks` | Your `.epub` / `.pdf` / `.cbz` / `.mobi` files. Read-only from despereaux's POV. |
| `./data/config` (default) | `/config` | SQLite DB, covers, page + metadata caches. Back this up. |

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

## Chaptarr integration

Two complementary mechanisms keep the library in sync:

1. **Filesystem watcher** (always on). Despereaux runs `watchfiles.awatch` over the library mount. New files are picked up within ~3 s (after the size stops changing, so half-downloaded files aren't ingested). Deletes remove the row. This works regardless of who put the file there — chaptarr, Calibre, scp, manual drop.

2. **Webhook** `POST /api/admin/sync` for instant chaptarr (or Readarr) "on import" triggers.
   ```bash
   curl -X POST http://despereaux:8000/api/admin/sync \
     -H "Authorization: Bearer ${DESPEREAUX_WEBHOOK_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"paths":["/ebooks/Author/Book.epub"]}'
   ```
   Empty `paths` → queues a full scan; non-empty → targeted ingest of those files only. Token comes from `DESPEREAUX_WEBHOOK_TOKEN` env (endpoint returns 503 if unset).

### Wire it into chaptarr's container

The media-server compose has been updated so chaptarr can call the webhook:
- `../despereaux/scripts:/scripts:ro` is mounted into chaptarr
- `DESPEREAUX_URL=http://despereaux:8000` and `DESPEREAUX_TOKEN=${DESPEREAUX_WEBHOOK_TOKEN}` are set in chaptarr's env
- `MOUNT_TRANSLATE_FROM=/books` → `MOUNT_TRANSLATE_TO=/ebooks` translates chaptarr's mount point to despereaux's

In chaptarr's UI: **Settings → Connect → + Custom Script**, Path = `/scripts/notify-despereaux.sh`, Triggers = On Import + On Upgrade. Use the **Test** button — it pings despereaux's `/healthz`.

Set `DESPEREAUX_WEBHOOK_TOKEN` in your deploy box's environment (or `.env`) so both services see it:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Add the output as DESPEREAUX_WEBHOOK_TOKEN=... in the media-server .env
```

If chaptarr doesn't actually surface a Custom Scripts UI (it's an alpha Readarr fork), the filesystem watcher alone is enough — chaptarr → file lands in `${MEDIA_MOUNT}/Ebooks` → despereaux watcher → ingested in ~3 s.

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
