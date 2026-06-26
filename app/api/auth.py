# app/api/auth.py
import hashlib
import hmac
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.exceptions import (
    AlreadyExistsError,
    AuthError,
    TokenExpiredError,
    TokenInvalidError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_referral_code,
    hash_password,
    verify_password,
    verify_webapp_init_data,
)
from app.db.models import User, UserRole, UserSession
from app.db.session import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    referral_code: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TelegramAuthRequest(BaseModel):
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str


class MiniAppAuthRequest(BaseModel):
    init_data: str   # raw Telegram.WebApp.initData query string


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    role: str
    is_admin: bool = False


class UserResponse(BaseModel):
    id: str
    email: Optional[str]
    full_name: Optional[str]
    username: Optional[str]
    telegram_id: Optional[int]
    role: str
    is_verified: bool
    referral_code: Optional[str]
    language_code: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Duplicate email check
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise AlreadyExistsError("User", "email")

    # Referral bonus lookup
    referrer: Optional[User] = None
    if body.referral_code:
        ref_result = await db.execute(
            select(User).where(User.referral_code == body.referral_code.upper())
        )
        referrer = ref_result.scalar_one_or_none()

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=UserRole.owner,
        referral_code=generate_referral_code(),
        referred_by_id=referrer.id if referrer else None,
    )
    db.add(user)
    await db.flush()

    access_token = create_access_token(user.id, role=user.role.value)
    refresh_token = create_refresh_token(user.id)

    session = UserSession(
        user_id=user.id,
        refresh_token=refresh_token,
        expires_at=_refresh_expires(),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        platform="web",
    )
    db.add(session)

    logger.info("User registered", user_id=str(user.id), email=body.email)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=str(user.id),
        role=user.role.value,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or user.hashed_password is None:
        raise AuthError("Invalid email or password")
    if not verify_password(body.password, user.hashed_password):
        raise AuthError("Invalid email or password")
    if not user.is_active:
        raise AuthError("Account suspended")

    access_token = create_access_token(user.id, role=user.role.value)
    refresh_token = create_refresh_token(user.id)

    session = UserSession(
        user_id=user.id,
        refresh_token=refresh_token,
        expires_at=_refresh_expires(),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        platform="web",
    )
    db.add(session)

    logger.info("User logged in", user_id=str(user.id))
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=str(user.id),
        role=user.role.value,
    )


@router.post("/telegram", response_model=TokenResponse)
async def telegram_login(
    body: TelegramAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Telegram Login Widget callback.
    Verifies the HMAC hash per Telegram docs, then upserts the User.
    """
    _verify_telegram_auth(body)

    result = await db.execute(select(User).where(User.telegram_id == body.id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            telegram_id=body.id,
            username=body.username,
            full_name=f"{body.first_name} {body.last_name or ''}".strip(),
            avatar_url=body.photo_url,
            role=UserRole.owner,
            referral_code=generate_referral_code(),
            is_verified=True,
        )
        db.add(user)
        await db.flush()
        logger.info("New Telegram user registered", telegram_id=body.id)
    else:
        # Refresh profile fields
        user.username = body.username or user.username
        user.avatar_url = body.photo_url or user.avatar_url

    if not user.is_active:
        raise AuthError("Account suspended")

    access_token = create_access_token(user.id, role=user.role.value)
    refresh_token = create_refresh_token(user.id)

    session = UserSession(
        user_id=user.id,
        refresh_token=refresh_token,
        expires_at=_refresh_expires(),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        platform="telegram",
    )
    db.add(session)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=str(user.id),
        role=user.role.value,
    )


@router.post("/miniapp", response_model=TokenResponse)
async def miniapp_login(
    body: MiniAppAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Telegram Mini App auth (OWNER console on the master bot).
    Validates initData against the MASTER bot token, upserts the user, and
    returns the platform's normal JWTs. Customer storefront auth is a SEPARATE
    path that validates against the BUSINESS bot token — never this endpoint.
    """
    if not settings.master_bot_token:
        raise AuthError("Master bot not configured")

    data = verify_webapp_init_data(body.init_data, settings.master_bot_token)
    tg = data.get("user") if data else None
    if not tg or not isinstance(tg, dict) or tg.get("id") is None:
        raise AuthError("Invalid Telegram Mini App data")

    tg_id = tg["id"]
    result = await db.execute(select(User).where(User.telegram_id == tg_id))
    user = result.scalar_one_or_none()

    if user is None:
        first = tg.get("first_name", "")
        last = tg.get("last_name", "")
        user = User(
            telegram_id=tg_id,
            username=tg.get("username"),
            full_name=f"{first} {last}".strip() or None,
            avatar_url=tg.get("photo_url"),
            language_code=tg.get("language_code") or "en",
            role=UserRole.owner,
            referral_code=generate_referral_code(),
            is_verified=True,
        )
        db.add(user)
        await db.flush()
        logger.info("New Mini App user registered", telegram_id=tg_id)
    else:
        user.username = tg.get("username") or user.username
        if tg.get("photo_url"):
            user.avatar_url = tg.get("photo_url")

    if not user.is_active:
        raise AuthError("Account suspended")

    access_token = create_access_token(user.id, role=user.role.value)
    refresh = create_refresh_token(user.id)
    db.add(UserSession(
        user_id=user.id,
        refresh_token=refresh,
        expires_at=_refresh_expires(),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        platform="telegram_miniapp",
    ))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh,
        user_id=str(user.id),
        role=user.role.value,
        is_admin=bool(getattr(user, "is_admin", False) or user.role == UserRole.admin),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise TokenInvalidError()

    if payload.get("type") != "refresh":
        raise TokenInvalidError()

    result = await db.execute(
        select(UserSession).where(UserSession.refresh_token == body.refresh_token)
    )
    session = result.scalar_one_or_none()
    if session is None or not session.is_valid:
        raise TokenExpiredError()

    user_result = await db.execute(select(User).where(User.id == UUID(payload["sub"])))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive")

    # Rotate: revoke old session, issue new tokens
    from datetime import datetime, timezone
    session.revoked_at = datetime.now(timezone.utc)

    new_access = create_access_token(user.id, role=user.role.value)
    new_refresh = create_refresh_token(user.id)

    new_session = UserSession(
        user_id=user.id,
        refresh_token=new_refresh,
        expires_at=_refresh_expires(),
        platform=session.platform,
    )
    db.add(new_session)

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        user_id=str(user.id),
        role=user.role.value,
    )


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(UserSession).where(UserSession.refresh_token == body.refresh_token)
    )
    session = result.scalar_one_or_none()
    if session and session.revoked_at is None:
        from datetime import datetime, timezone
        session.revoked_at = datetime.now(timezone.utc)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        username=current_user.username,
        telegram_id=current_user.telegram_id,
        role=current_user.role.value,
        is_verified=current_user.is_verified,
        referral_code=current_user.referral_code,
        language_code=current_user.language_code,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_telegram_auth(data: TelegramAuthRequest) -> None:
    """Validate Telegram Login Widget data per official docs."""
    # auth_date must be within 24 hours
    if abs(time.time() - data.auth_date) > 86400:
        raise AuthError("Telegram auth data expired")

    check_string = "\n".join(
        f"{k}={v}"
        for k, v in sorted(
            {
                "id": data.id,
                "first_name": data.first_name,
                "last_name": data.last_name or "",
                "username": data.username or "",
                "photo_url": data.photo_url or "",
                "auth_date": data.auth_date,
            }.items()
        )
        if v  # skip empty values
    )

    secret = hashlib.sha256(settings.master_bot_token.encode()).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, data.hash):
        raise AuthError("Telegram auth verification failed")


def _refresh_expires():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
