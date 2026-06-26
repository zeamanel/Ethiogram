# app/api/deps.py
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AdminRequiredError,
    AuthError,
    TokenExpiredError,
    TokenInvalidError,
)
from app.core.security import decode_access_token, verify_admin_secret_header
from app.db.models import User, UserRole
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise AuthError()
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError()
    except jwt.InvalidTokenError:
        raise TokenInvalidError()

    user_id: str = payload["sub"]
    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive")
    return user


async def get_current_admin(
    user: Annotated[User, Depends(get_current_user)],
    x_ethiogram_admin: Annotated[str | None, Header(alias="X-Ethiogram-Admin")] = None,
) -> User:
    if user.role not in (UserRole.admin, UserRole.support):
        raise AdminRequiredError()
    if not verify_admin_secret_header(x_ethiogram_admin):
        raise AdminRequiredError()
    return user


async def get_current_admin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Admin gate for the Mini App admin dashboard — a logged-in user whose
    is_admin flag is set (or legacy role='admin'). No server secret header, so
    the Telegram-authenticated admin can use it directly."""
    if not (getattr(user, "is_admin", False) or user.role == UserRole.admin):
        raise AdminRequiredError()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdmin = Annotated[User, Depends(get_current_admin)]
CurrentAdminUser = Annotated[User, Depends(get_current_admin_user)]
