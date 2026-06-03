from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from despereaux.api import admin as admin_api
from despereaux.api import books as books_api
from despereaux.api import libraries as libraries_api
from despereaux.api import metadata as metadata_api
from despereaux.api import progress as progress_api
from despereaux.api import stream as stream_api
from despereaux.config import get_settings
from despereaux.db import apply_sqlite_pragmas
from despereaux.middleware.auth import AuthentikUserMiddleware
from despereaux.services.scanner import scanner
from despereaux.web import routes as web_routes

settings = get_settings()

# Strong refs to fire-and-forget asyncio tasks so the GC doesn't reap them mid-run.
_background_tasks: set[asyncio.Task] = set()


def _configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def _run_migrations() -> None:
    """Run Alembic upgrade head from the project root containing alembic.ini."""
    from alembic.config import Config

    from alembic import command

    project_root = _find_project_root()
    ini_path = project_root / "alembic.ini"
    if not ini_path.exists():
        logging.warning("alembic.ini not found at %s; skipping migrations", ini_path)
        return
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.db_url)
    command.upgrade(cfg, "head")


def _find_project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "alembic.ini").exists():
            return parent
    return Path(os.environ.get("DESPEREAUX_PROJECT_ROOT", "/app"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    log = logging.getLogger("despereaux")
    log.info("starting up; library=%s data=%s", settings.library_path, settings.data_dir)

    # Run migrations (sync, blocking) then SQLite pragmas (async).
    await asyncio.to_thread(_run_migrations)
    await apply_sqlite_pragmas()

    # Kick off a one-shot scan in the background; don't block startup.
    # Skip the scan only if NO configured library exists on disk.
    library_paths_exist = any(lib.path.exists() for lib in settings.libraries)
    if library_paths_exist:
        task = asyncio.create_task(scanner.run_once(), name="initial-scan")
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        scanner.start_watcher()
        log.info(
            "scanner started for %d library(s): %s",
            len(settings.libraries),
            ", ".join(f"{lib.name}={lib.path}" for lib in settings.libraries),
        )
    else:
        log.warning(
            "no configured library path exists on disk; skipping scan + watcher (configured: %s)",
            [str(lib.path) for lib in settings.libraries],
        )

    yield
    log.info("shutting down")
    await scanner.stop_watcher()


def create_app() -> FastAPI:
    app = FastAPI(
        title="despereaux",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.add_middleware(AuthentikUserMiddleware)

    app.include_router(books_api.router)
    app.include_router(libraries_api.router)
    app.include_router(metadata_api.router)
    app.include_router(progress_api.router)
    app.include_router(stream_api.router)
    app.include_router(admin_api.router)
    app.include_router(web_routes.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        # Browsers that ignore <link rel="icon"> still hit /favicon.ico —
        # redirect to the mascot PNG.
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/static/img/mascot.png", status_code=302)

    return app


app = create_app()
