"""Admin endpoints for managing user subscription tiers.

Gated by ``settings.admin_usernames`` — only listed usernames may list users
or change a user's tier. This is intentionally lightweight (no separate admin
role/table): the operator lists their own username in config.yaml and gains
access. When the list is empty, no one can change tiers through the API (free
limits still apply to all users). Future gated features (continuous summary,
templates, 24/7 support) are unlocked by tier, not by this endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .config import get_settings
from .db import get_session
from .logging_config import get_logger
from .models import User
from .tiers import Tier, is_valid_tier

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Allow only usernames listed in ``settings.admin_usernames``."""
    admins = get_settings().admin_usernames or []
    if user.username not in admins:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    tier: str
    usage_minutes: int


class TierUpdate(BaseModel):
    tier: str


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    q: str | None = None,
    limit: int = 20,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminUserOut]:
    """List users, optionally filtered by a username search term.

    Pass ``q`` to match usernames case-insensitively (substring); results are
    capped at ``limit`` so a search never streams the whole user table. Without
    ``q`` the (capped) full list is returned for admin tooling.
    """
    stmt = select(User)
    if q:
        # Escape LIKE wildcards in the user input so a literal "%" or "_" in a
        # search term isn't treated as a pattern.
        like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(User.username.ilike(f"%{like}%", escape="\\"))
    stmt = stmt.order_by(User.username).limit(max(1, min(limit, 100)))
    logger.info("Fetching users", query=q, limit=limit)
    result = await session.execute(stmt)
    users = [AdminUserOut.model_validate(u) for u in result.scalars().all()]
    logger.info("Fetched %d users", len(users))
    return users


@router.put("/users/{user_id}/tier", response_model=AdminUserOut)
async def set_user_tier(
    user_id: str,
    body: TierUpdate,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserOut:
    if not is_valid_tier(body.tier):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid tier. Must be one of: "
            f"{', '.join(t.value for t in Tier)}.",
        )
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    logger.info("Updating user tier", user_id=user_id, new_tier=body.tier)
    target.tier = body.tier
    await session.commit()
    logger.info("User tier updated", user_id=user_id, new_tier=body.tier)
    return AdminUserOut.model_validate(target)
