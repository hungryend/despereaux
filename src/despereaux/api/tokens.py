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
