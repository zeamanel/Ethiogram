# app/api/account.py
"""My Account — the end-user's own profile for the platform bot Mini App.

Returns the logged-in user's name, language, ETG balance, referral code/link,
and the businesses they own. Auth is the same JWT used by the owner console
(exchanged from Telegram initData via /auth/miniapp).
"""
import secrets

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.config import settings
from app.db.models import Business, User
from app.db.session import get_db

router = APIRouter(prefix="/account", tags=["account"])

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I


class AccountBusiness(BaseModel):
    id: str
    name: str
    slug: str
    store_url: str


class AccountResponse(BaseModel):
    name: str
    username: str | None
    language_code: str
    etg_balance: int
    referral_code: str
    referral_link: str
    businesses: list[AccountBusiness]


def _base() -> str:
    return (settings.base_url or "https://api.ethiogram.com").rstrip("/")


async def _ensure_referral_code(user, db: AsyncSession) -> str:
    """Return the user's referral code, minting a unique one on first view."""
    if user.referral_code:
        return user.referral_code
    for _ in range(8):
        code = "".join(secrets.choice(_REF_ALPHABET) for _ in range(8))
        taken = await db.scalar(select(User.id).where(User.referral_code == code))
        if not taken:
            user.referral_code = code
            await db.flush()
            return code
    return user.referral_code or ""


@router.get("/me", response_model=AccountResponse)
async def get_my_account(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    rows = (await db.execute(
        select(Business).where(
            Business.owner_id == user.id,
            Business.deleted_at.is_(None),
        ).order_by(Business.created_at.asc())
    )).scalars().all()

    base = _base()
    businesses = [
        AccountBusiness(id=str(b.id), name=b.name, slug=b.slug,
                        store_url=f"{base}/app/store/?s={b.slug}")
        for b in rows
    ]

    code = await _ensure_referral_code(user, db)
    link = f"https://t.me/{settings.master_bot_username}?start=ref_{code}"

    return AccountResponse(
        name=user.full_name or user.username or "there",
        username=user.username,
        language_code=user.language_code or "en",
        etg_balance=user.etg_balance or 0,
        referral_code=code,
        referral_link=link,
        businesses=businesses,
    )
