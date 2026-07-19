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
from .models import User
from .tiers import Tier, is_valid_tier

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
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminUserOut]:
    result = await session.execute(select(User).order_by(User.created_at))
    return [AdminUserOut.model_validate(u) for u in result.scalars().all()]


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
    target.tier = body.tier
    await session.commit()
    return AdminUserOut.model_validate(target)
