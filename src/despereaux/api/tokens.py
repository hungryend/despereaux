from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.db import get_db
from despereaux.middleware.auth import current_user, require_admin
from despereaux.models import User
from despereaux.repos import api_tokens as tokens_repo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/me")
async def me(user=Depends(current_user)):
    """Identity probe — lets a client verify its token and show who's signed in."""
    return user


# ---- Self-service: any signed-in user manages their OWN tokens (Account page). ----


class OwnTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, description="Label, e.g. 'my-phone'.")


@router.get("/tokens")
async def list_own_tokens(
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    rows = await tokens_repo.list_api_tokens_for_user(session, user["id"])
    return [
        {
            "id": row.id,
            "name": row.name,
            "created_at": row.created_at,
            "last_used_at": row.last_used_at,
        }
        for row in rows
    ]


@router.post("/tokens", status_code=201)
async def create_own_token(
    body: OwnTokenCreateRequest,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    """Mint a token for the calling user. The plaintext appears in this response only."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must be non-empty")
    row, plaintext = await tokens_repo.create_api_token(session, user_id=user["id"], name=name)
    await session.commit()
    log.info("api token self-minted: user=%s name=%s", user["username"], name)
    return {"id": row.id, "username": user["username"], "name": name, "token": plaintext}


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_own_token(
    token_id: str,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    row = await tokens_repo.get_api_token(session, token_id)
    # 404 (not 403) for someone else's token: don't confirm the id exists.
    if row is None or row.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="token not found")
    await tokens_repo.delete_api_token(session, token_id)
    await session.commit()
    log.info("api token self-revoked: user=%s id=%s", user["username"], token_id)


class TokenCreateRequest(BaseModel):
    username: str = Field(min_length=1, description="Owner of the token; created if unknown.")
    name: str = Field(min_length=1, description="Label, e.g. 'alex-pixel8'.")


@router.get("/admin/tokens")
async def list_tokens(
    session: AsyncSession = Depends(get_db),
    _admin=Depends(require_admin),
):
    rows = await tokens_repo.list_api_tokens(session)
    return [
        {
            "id": row.id,
            "username": username,
            "name": row.name,
            "created_at": row.created_at,
            "last_used_at": row.last_used_at,
        }
        for row, username in rows
    ]


@router.post("/admin/tokens", status_code=201)
async def create_token(
    body: TokenCreateRequest,
    session: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    """Mint a token for a user. The plaintext appears in this response only.

    Unknown usernames get a fresh user with NO groups (not admin) — lets the
    admin provision app-only accounts that have never logged in via Authentik.
    Deliberately NOT get_or_create_user: that would overwrite an existing
    user's groups.
    """
    username = body.username.strip()
    name = body.name.strip()
    if not username or not name:
        raise HTTPException(status_code=422, detail="username and name must be non-empty")

    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(username=username, email=None, authentik_groups=[])
        session.add(user)
        await session.flush()

    row, plaintext = await tokens_repo.create_api_token(session, user_id=user.id, name=name)
    await session.commit()
    log.info("api token created: user=%s name=%s by=%s", username, name, admin["username"])
    return {"id": row.id, "username": username, "name": name, "token": plaintext}


@router.delete("/admin/tokens/{token_id}", status_code=204)
async def revoke_token(
    token_id: str,
    session: AsyncSession = Depends(get_db),
    admin=Depends(require_admin),
):
    deleted = await tokens_repo.delete_api_token(session, token_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="token not found")
    await session.commit()
    log.info("api token revoked: id=%s by=%s", token_id, admin["username"])
