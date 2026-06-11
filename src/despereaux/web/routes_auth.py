from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.config import get_settings
from despereaux.db import get_db, session_scope
from despereaux.middleware.auth import current_user, require_admin, reset_setup_cache
from despereaux.models import User
from despereaux.repos.users import (
    any_native_admin_exists,
    create_user,
    get_user_by_username,
    list_users_with_token_counts,
)
from despereaux.services.passwords import hash_password, validate_password, verify_password
from despereaux.services.sessions import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    make_session_token,
)
from despereaux.web.routes import templates

log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

# Templates can ask which auth mode is active (e.g. to show the Sign out link).
templates.env.globals["auth_mode"] = lambda: get_settings().auth_mode
templates.env.globals["admin_group"] = lambda: get_settings().admin_group


def _native_only() -> None:
    if get_settings().auth_mode != "native":
        raise HTTPException(status_code=404, detail="not available in authentik mode")


def _set_session_cookie(response: Response, request: Request, user_id: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        make_session_token(user_id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


# ---------------- login / logout / first-run setup (native mode) ----------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: AsyncSession = Depends(get_db)):
    if get_settings().auth_mode != "native":
        return RedirectResponse(url="/", status_code=302)
    if not await any_native_admin_exists(session):
        return RedirectResponse(url="/setup", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    _native_only()
    async with session_scope() as session:
        user = await get_user_by_username(session, username.strip())
        if user is None or not verify_password(password, user.password_hash):
            log.info("failed login attempt for username=%r", username.strip())
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Invalid username or password."},
                status_code=401,
            )
        user_id = user.id
    response = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(response, request, user_id)
    return response


@router.get("/logout")
async def logout():
    target = "/login" if get_settings().auth_mode == "native" else "/"
    response = RedirectResponse(url=target, status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, session: AsyncSession = Depends(get_db)):
    _native_only()
    if await any_native_admin_exists(session):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
async def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """First-run admin creation. Locked as soon as one login-capable admin exists."""
    _native_only()
    username = username.strip()
    error = validate_password(password) if username else "username is required"
    async with session_scope() as session:
        if await any_native_admin_exists(session):
            return RedirectResponse(url="/login", status_code=303)
        if not error and await get_user_by_username(session, username):
            error = "that username already exists"
        if error:
            return templates.TemplateResponse(
                request, "setup.html", {"error": error}, status_code=422
            )
        user = await create_user(
            session,
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
        )
        user_id = user.id
    reset_setup_cache()
    log.info("native auth set up; admin user %r created", username)
    response = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(response, request, user_id)
    return response


# ---------------- account page (both modes) ----------------


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
    msg: str | None = None,
):
    row = await session.get(User, user["id"])
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "user": user,
            "has_password": bool(row is not None and row.password_hash),
            "msg": msg,
            "error": None,
        },
    )


@router.post("/account/password")
async def change_own_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(...),
    user=Depends(current_user),
):
    _native_only()
    async with session_scope() as session:
        row = await session.get(User, user["id"])
        if row is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if row.password_hash and not verify_password(current_password, row.password_hash):
            error = "current password is incorrect"
        else:
            error = validate_password(new_password)
        if error:
            return templates.TemplateResponse(
                request,
                "account.html",
                {
                    "user": user,
                    "has_password": bool(row.password_hash),
                    "msg": None,
                    "error": error,
                },
                status_code=422,
            )
        row.password_hash = hash_password(new_password)
    log.info("password changed for user=%s", user["username"])
    return RedirectResponse(url="/account?msg=Password+changed", status_code=303)


# ---------------- admin: user management ----------------


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
    msg: str | None = None,
    error: str | None = None,
):
    rows = await list_users_with_token_counts(session)
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "user": admin,
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "email": u.email,
                    "is_admin": u.is_admin,
                    "groups": list(u.authentik_groups or []),
                    "has_password": bool(u.password_hash),
                    "token_count": count,
                    "created_at": u.created_at,
                    "last_seen_at": u.last_seen_at,
                }
                for u, count in rows
            ],
            "msg": msg,
            "error": error,
        },
    )


def _redirect_admin(msg: str | None = None, error: str | None = None) -> RedirectResponse:
    from urllib.parse import quote_plus

    q = f"?msg={quote_plus(msg)}" if msg else (f"?error={quote_plus(error)}" if error else "")
    return RedirectResponse(url=f"/admin/users{q}", status_code=303)


@router.post("/admin/users/create")
async def admin_create_user(
    username: str = Form(...),
    password: str = Form(""),
    is_admin: bool = Form(False),
    admin=Depends(require_admin),
):
    """Create a user. Password optional: without one the account is header/token-only
    (authentik mode, or app-only users); with one it can log in natively."""
    username = username.strip()
    if not username:
        return _redirect_admin(error="username is required")
    if password:
        pw_error = validate_password(password)
        if pw_error:
            return _redirect_admin(error=pw_error)
    async with session_scope() as session:
        if await get_user_by_username(session, username):
            return _redirect_admin(error="that username already exists")
        await create_user(
            session,
            username=username,
            password_hash=hash_password(password) if password else None,
            is_admin=is_admin,
        )
    reset_setup_cache()
    log.info("user %r created by admin %s (admin=%s)", username, admin["username"], is_admin)
    return _redirect_admin(msg=f"User {username} created")


@router.post("/admin/users/{user_id}/password")
async def admin_set_password(
    user_id: str,
    password: str = Form(...),
    admin=Depends(require_admin),
):
    pw_error = validate_password(password)
    if pw_error:
        return _redirect_admin(error=pw_error)
    async with session_scope() as session:
        row = await session.get(User, user_id)
        if row is None:
            return _redirect_admin(error="user not found")
        row.password_hash = hash_password(password)
        username = row.username
    log.info("password set for user=%s by admin %s", username, admin["username"])
    return _redirect_admin(msg=f"Password set for {username}")


@router.post("/admin/users/{user_id}/admin")
async def admin_toggle_admin(
    user_id: str,
    make_admin: bool = Form(...),
    admin=Depends(require_admin),
):
    if user_id == admin["id"] and not make_admin:
        return _redirect_admin(error="you can't remove your own admin role")
    async with session_scope() as session:
        row = await session.get(User, user_id)
        if row is None:
            return _redirect_admin(error="user not found")
        row.is_admin = make_admin
        username = row.username
    log.info("admin=%s for user=%s by %s", make_admin, username, admin["username"])
    return _redirect_admin(msg=f"{username} is {'now' if make_admin else 'no longer'} an admin")
