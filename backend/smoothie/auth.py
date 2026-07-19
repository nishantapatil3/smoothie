"""Authentication: user accounts, password hashing, and signed Bearer tokens.

Access tokens are stateless — a base64url-encoded JSON payload (user id + expiry)
signed with HMAC-SHA256 using ``settings.auth_secret``. They are short-lived
(``auth_access_ttl_minutes``) and refreshed silently. Refresh tokens are opaque
random values whose *hash* is stored in the ``refresh_tokens`` table; each use
rotates them (the old one is revoked) and logout revokes them, so a leaked
refresh token has a bounded lifetime and can be invalidated.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import RefreshToken, User

logger = logging.getLogger("smoothie")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------------- #
# Password hashing (bcrypt)
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Return a bcrypt hash of ``password``."""
    pw = password.encode("utf-8")
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check ``password`` against a bcrypt ``password_hash``."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# Token signing (HMAC-SHA256)
# --------------------------------------------------------------------------- #
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _secret() -> bytes:
    settings = get_settings()
    if not settings.auth_secret:
        # Ephemeral secret: tokens minted this run won't validate after restart.
        logger.warning(
            "auth_secret is unset in config.yaml; generating an ephemeral "
            "signing secret. Set a stable auth_secret so tokens survive "
            "restarts."
        )
        settings.auth_secret = secrets.token_hex(32)
    return settings.auth_secret.encode("utf-8")


def generate_token(user_id: str) -> str:
    """Create a short-lived signed access token (Bearer) for ``user_id``."""
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.auth_access_ttl_minutes
    )
    payload = {"sub": user_id, "exp": int(expires.timestamp())}
    body = _b64url_encode(json.dumps(payload).encode("utf-8"))
    sig = _b64url_encode(
        hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
    """Return the embedded user id if the access token is valid, else ``None``."""
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None

    expected = _b64url_encode(
        hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, sig):
        return None

    try:
        payload = json.loads(_b64url_decode(body))
        exp = payload.get("exp")
        if exp is None or datetime.now(timezone.utc).timestamp() > exp:
            return None
        return payload.get("sub")
    except (ValueError, TypeError, binascii.Error):
        return None


# --------------------------------------------------------------------------- #
# Refresh tokens (stored hashed, rotated on use, revocable)
# --------------------------------------------------------------------------- #
def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_refresh_token(
    session: AsyncSession, user_id: str
) -> str:
    """Create, persist, and return a new opaque refresh token for ``user_id``."""
    settings = get_settings()
    raw = secrets.token_urlsafe(48)
    token = RefreshToken(
        user_id=user_id,
        token_hash=_hash_refresh(raw),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.auth_refresh_ttl_days),
    )
    session.add(token)
    await session.commit()
    return raw


async def rotate_refresh_token(
    session: AsyncSession, raw_token: str
) -> tuple[str, str] | None:
    """Validate ``raw_token``, rotate it, and return (new_access, new_refresh).

    The presented refresh token is revoked (deleted) and a fresh pair is
    issued, following the rotate-on-use pattern. Returns ``None`` if the token
    is unknown, expired, or whose user no longer exists.
    """
    token_hash = _hash_refresh(raw_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored is None:
        return None
    # SQLite returns tz-naive datetimes; normalize to aware UTC before compare.
    expires = stored.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        await session.delete(stored)
        await session.commit()
        return None

    user = await session.get(User, stored.user_id)
    if user is None:
        await session.delete(stored)
        await session.commit()
        return None

    # Revoke the old refresh token, then issue a brand-new pair.
    await session.delete(stored)
    new_access = generate_token(user.id)
    new_refresh = await issue_refresh_token(session, user.id)
    return new_access, new_refresh


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    """Delete a single refresh token (used by logout)."""
    token_hash = _hash_refresh(raw_token)
    await session.execute(
        delete(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    await session.commit()


async def revoke_all_user_tokens(session: AsyncSession, user_id: str) -> None:
    """Delete every refresh token for a user (logout-everywhere)."""
    await session.execute(
        delete(RefreshToken).where(RefreshToken.user_id == user_id)
    )
    await session.commit()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class RegisterIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class TokenOut(BaseModel):
    token: str
    refresh_token: str


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    created_at: datetime


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #
async def get_current_user(
    session: AsyncSession = Depends(get_session),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """Resolve the authenticated user from the Bearer token, or 401."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    user_id = verify_token(creds.credentials)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return user


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.post("/register", response_model=TokenOut, status_code=201)
async def register(
    body: RegisterIn, session: AsyncSession = Depends(get_session)
) -> TokenOut:
    settings = get_settings()
    if len(body.password) < settings.auth_min_password_len:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least "
            f"{settings.auth_min_password_len} characters.",
        )

    existing = await session.execute(
        select(User).where(User.username == body.username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username already taken.")

    user = User(
        id=uuid.uuid4().hex,
        username=body.username,
        password_hash=hash_password(body.password),
    )
    session.add(user)
    await session.commit()
    refresh = await issue_refresh_token(session, user.id)
    return TokenOut(token=generate_token(user.id), refresh_token=refresh)


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn, session: AsyncSession = Depends(get_session)
) -> TokenOut:
    result = await session.execute(
        select(User).where(User.username == body.username)
    )
    user = result.scalar_one_or_none()
    # Verify even when the user is missing to keep timing roughly constant.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    refresh = await issue_refresh_token(session, user.id)
    return TokenOut(token=generate_token(user.id), refresh_token=refresh)


@router.post("/refresh", response_model=TokenOut)
async def refresh(
    body: RefreshIn, session: AsyncSession = Depends(get_session)
) -> TokenOut:
    """Exchange a valid refresh token for a fresh access + refresh pair.

    The presented refresh token is revoked (rotate-on-use). Returns 401 if the
    token is unknown, expired, or whose account no longer exists.
    """
    pair = await rotate_refresh_token(session, body.refresh_token)
    if pair is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token.")
    new_access, new_refresh = pair
    return TokenOut(token=new_access, refresh_token=new_refresh)


@router.post("/logout")
async def logout(
    body: RefreshIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Revoke the presented refresh token for the authenticated user."""
    await revoke_refresh_token(session, body.refresh_token)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
