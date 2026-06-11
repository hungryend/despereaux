from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from despereaux.config import get_settings
from despereaux.db import session_scope
from despereaux.models import User
from despereaux.repos.api_tokens import resolve_api_token
from despereaux.repos.users import any_native_admin_exists, get_or_create_user
from despereaux.services.sessions import SESSION_COOKIE, verify_session_token

settings = get_settings()

# Paths that bypass auth (no user attached).
# /api/admin/sync uses a shared token instead of Authentik (internal Docker callers).
# /login, /setup and /logout must be reachable anonymously in native mode.
_PUBLIC_PATHS = (
    "/healthz",
    "/static/",
    "/favicon.ico",
    "/api/admin/sync",
    "/login",
    "/logout",
    "/setup",
)

# Cookie fallback for clients that can't attach an Authorization header to every
# request — concretely the Furlough WebView reader, whose subresource requests
# (epub.js/PDF.js fetches under /read and /api) carry cookies but not headers.
TOKEN_COOKIE = "despereaux_token"

# Native mode: have we seen a login-capable admin yet? Cached so anonymous
# requests don't hit the DB just to decide between /login and /setup.
# reset_setup_cache() flips it after first-run setup (and in tests).
_native_admin_known: bool = False


def reset_setup_cache() -> None:
    global _native_admin_known
    _native_admin_known = False


def _parse_groups(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [g.strip() for g in raw.split(",") if g.strip()]


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip() or None


def _wants_html(request: Request) -> bool:
    if request.url.path.startswith("/api"):
        return False
    return "text/html" in request.headers.get("accept", "")


def _attach(request: Request, user: User) -> None:
    request.state.user_id = user.id
    request.state.user_username = user.username
    request.state.user_groups = list(user.authentik_groups or [])
    request.state.user_is_admin = bool(user.is_admin) or (
        settings.admin_group in (user.authentik_groups or [])
    )


class AuthUserMiddleware(BaseHTTPMiddleware):
    """Resolves the request's user. The identity source depends on AUTH_MODE:

    authentik (default): `X-authentik-*` headers from a forward-auth reverse
      proxy. The proxy is the gate; despereaux just maps headers to a user.
    native: despereaux's own signed session cookie (set by /login). The
      X-authentik-* headers are IGNORED — without a trusted proxy they are
      client-controlled and would allow impersonating anyone.

    In BOTH modes a per-user API token is accepted next, via
    `Authorization: Bearer <token>` or the `despereaux_token` cookie (native
    apps — Furlough). An explicitly presented but invalid token is a hard 401
    even in dev mode, so a typo'd token can't silently turn into `devuser`.
    Last, dev mode auto-creates an admin `devuser` (local dev / tests).
    """

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in _PUBLIC_PATHS):
            return await call_next(request)

        if settings.auth_mode == "authentik":
            username = request.headers.get("x-authentik-username")
            if username:
                email = request.headers.get("x-authentik-email")
                groups = _parse_groups(request.headers.get("x-authentik-groups"))
                async with session_scope() as session:
                    user = await get_or_create_user(
                        session, username=username, email=email, groups=groups
                    )
                    _attach(request, user)
                return await call_next(request)
        else:  # native
            session_uid = verify_session_token(request.cookies.get(SESSION_COOKIE))
            if session_uid:
                async with session_scope() as session:
                    user = await session.get(User, session_uid)
                    if user is not None:
                        _attach(request, user)
                if hasattr(request.state, "user_id"):
                    return await call_next(request)

        token = _bearer_token(request) or request.cookies.get(TOKEN_COOKIE)
        if token:
            async with session_scope() as session:
                user = await resolve_api_token(session, token)
                if user is None:
                    return JSONResponse(
                        {"detail": "invalid or revoked API token"},
                        status_code=status.HTTP_401_UNAUTHORIZED,
                    )
                _attach(request, user)
            return await call_next(request)

        if settings.dev_mode:
            async with session_scope() as session:
                user = await get_or_create_user(
                    session,
                    username="devuser",
                    email="dev@local",
                    groups=[settings.admin_group],
                )
                _attach(request, user)
            return await call_next(request)

        if settings.auth_mode == "native" and _wants_html(request):
            return RedirectResponse(url=await _login_or_setup(), status_code=302)

        detail = (
            "not signed in"
            if settings.auth_mode == "native"
            else "no authentik headers; this endpoint must be reached via the forward-auth proxy"
        )
        return JSONResponse({"detail": detail}, status_code=status.HTTP_401_UNAUTHORIZED)


async def _login_or_setup() -> str:
    """First run of native mode (no login-capable admin) goes to /setup."""
    global _native_admin_known
    if _native_admin_known:
        return "/login"
    async with session_scope() as session:
        if await any_native_admin_exists(session):
            _native_admin_known = True
            return "/login"
    return "/setup"


# Backwards-compatible name (pre-native-auth imports).
AuthentikUserMiddleware = AuthUserMiddleware


def current_user(request: Request) -> dict:
    if not hasattr(request.state, "user_id"):
        raise HTTPException(status_code=401, detail="not authenticated")
    return {
        "id": request.state.user_id,
        "username": request.state.user_username,
        "groups": request.state.user_groups,
        "is_admin": request.state.user_is_admin,
    }


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="admin required")
    return user
