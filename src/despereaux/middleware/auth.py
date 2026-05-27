from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from despereaux.config import get_settings
from despereaux.db import session_scope
from despereaux.models import User
from despereaux.repos.users import get_or_create_user

settings = get_settings()

# Paths that bypass auth (no user attached).
# /api/admin/sync uses a shared token instead of Authentik (internal Docker callers).
_PUBLIC_PATHS = ("/healthz", "/static/", "/favicon.ico", "/api/admin/sync")


def _parse_groups(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [g.strip() for g in raw.split(",") if g.strip()]


class AuthentikUserMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in _PUBLIC_PATHS):
            return await call_next(request)

        username = request.headers.get("x-authentik-username")
        email = request.headers.get("x-authentik-email")
        groups = _parse_groups(request.headers.get("x-authentik-groups"))

        if not username:
            if settings.dev_mode:
                username, email, groups = "devuser", "dev@local", [settings.admin_group]
            else:
                return JSONResponse(
                    {"detail": "no authentik headers; this endpoint must be reached via SWAG"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

        async with session_scope() as session:
            user = await get_or_create_user(
                session, username=username, email=email, groups=groups
            )
            request.state.user_id = user.id
            request.state.user_username = user.username
            request.state.user_groups = list(user.authentik_groups or [])

        return await call_next(request)


def current_user(request: Request) -> dict:
    if not hasattr(request.state, "user_id"):
        raise HTTPException(status_code=401, detail="not authenticated")
    return {
        "id": request.state.user_id,
        "username": request.state.user_username,
        "groups": request.state.user_groups,
    }


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if settings.admin_group not in user["groups"]:
        raise HTTPException(status_code=403, detail="admin group required")
    return user
