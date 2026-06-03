# Contributing

Thanks for considering a contribution. This project keeps a low-ceremony workflow
intended for solo + small-team development.

## Working on changes

1. Open an issue first for anything non-trivial so we can agree on direction
   before code gets written.
2. Branch off `main`. Naming convention: `feat/<topic>`, `fix/<topic>`,
   `docs/<topic>`, `chore/<topic>`. One branch per logical change.
3. Make your changes, push the branch, open a pull request against `main`.
4. CI runs automatically on the PR (lint, tests, build, security scan).
   Fix anything red before requesting review.
5. PRs are squash-merged. The squash commit message becomes the single entry
   in `main`'s history — keep the PR title in
   [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) form.

## Conventional Commits

PR titles (which become squash-merge commit messages on `main`) follow:

```
<type>(<optional scope>): <short summary>
```

Common types:

| Type      | When to use                                                          |
|-----------|----------------------------------------------------------------------|
| `feat`    | A user-visible new capability                                        |
| `fix`     | A bug fix                                                            |
| `docs`    | README, comments, in-code docs                                       |
| `refactor`| Code restructuring with no behaviour change                          |
| `perf`    | Performance improvement                                              |
| `test`    | Tests-only changes                                                   |
| `build`   | Dockerfile, dependencies, packaging                                  |
| `ci`      | GitHub Actions workflows                                             |
| `chore`   | Maintenance with no source impact (release tags, dependabot config)  |

Examples:

- `feat(reader): predictive page prefetch with direction inference`
- `fix(ingest): skip cover render for PDFs over 200 MB`
- `docs: clarify reverse-proxy header requirements`
- `chore(deps): bump pdfjs-dist to 4.10.38`

Within the PR body, explain the *why*. Code shows the *what*; the message
should answer "what problem does this solve?".

## Running checks locally

```bash
# Python lint + format
uv run ruff check .
uv run ruff format .

# Python tests
uv run pytest -q

# Frontend build (catches TypeScript errors)
cd frontend && npm run build

# Full build (as CI does)
docker build -t despereaux:local .
```

## Releases

`main` is always releasable. The Release workflow pushes a new
`ghcr.io/hungryend/despereaux:latest` on every merge.

Cutting a versioned release:

1. Update the `[Unreleased]` section of `CHANGELOG.md`, move its contents
   under a new heading like `## [0.2.0] — YYYY-MM-DD`, fix the link refs
   at the bottom, and merge the changelog PR.
2. Tag from `main`:
   ```bash
   git checkout main && git pull
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```
3. The Release workflow publishes `:v0.2.0`, scans it, and creates a
   GitHub Release with auto-generated notes from the commit log.

## Code style notes

- Python is `ruff`-formatted, `from __future__ import annotations` everywhere
  for forward-compatible type hints.
- Pydantic v2 schemas live under `src/despereaux/schemas/`, SQLAlchemy
  ORM under `src/despereaux/models/`.
- Routes are thin; business logic in `services/`, data access in `repos/`.
- TypeScript is strict; no `any` unless you've left a comment explaining why.

## Issue triage

- `bug` — broken behaviour vs documented spec
- `enhancement` — a new capability or improvement to an existing one
- `docs` — improvements to README, CHANGELOG, this file
- `good first issue` — small, well-scoped, useful for newcomers
- `help wanted` — something I'd appreciate community help on

Be kind. Be specific. Include reproduction steps for bugs.
