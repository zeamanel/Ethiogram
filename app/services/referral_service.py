# app/services/referral_service.py
"""Owner-to-owner referral program.

Flow:
  1. An owner shares  https://t.me/<master_bot>?start=ref_<their_telegram_id>
  2. The friend taps it → the master bot's /start carries "ref_<id>" → we park
     the referral in Redis (30-day TTL) keyed by the friend's telegram id.
  3. When the friend connects their FIRST bot (the activation moment — a new
     TokenWallet is created), both sides get settings.etg_referral_bonus:
     the new business's wallet, and the referrer's first business's wallet.

Everything is best-effort: a failed referral credit must never break
onboarding, and self-referrals are ignored.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metering import metering_service
from app.db.models import Business, User

logger = get_logger(__name__)

_PENDING_TTL = 30 * 86400          # 30 days to activate after tapping the link


def _key(telegram_id) -> str:
    return f"ref:pending:{telegram_id}"


async def capture(redis, new_user_telegram_id, referrer_telegram_id) -> bool:
    """Park a referral when a /start ref_<id> arrives. Returns True if stored."""
    new_id, ref_id = str(new_user_telegram_id).strip(), str(referrer_telegram_id).strip()
    if not new_id or not ref_id.isdigit() or new_id == ref_id:
        return False                    # garbage payload or self-referral
    # First referrer wins — don't let a second link overwrite the original.
    existing = await redis.get(_key(new_id))
    if existing:
        return False
    await redis.set(_key(new_id), ref_id, ex=_PENDING_TTL)
    logger.info("Referral captured", referred=new_id, referrer=ref_id)
    return True


async def redeem(db: AsyncSession, redis, new_business_id, owner_telegram_id) -> bool:
    """Credit both sides after the referred owner connects their first bot.
    Returns True when a referral was found and paid out. Never raises."""
    if not owner_telegram_id:
        return False
    try:
        raw = await redis.get(_key(owner_telegram_id))
        if not raw:
            return False
        referrer_tg = int(raw if isinstance(raw, str) else raw.decode())

        referrer = (await db.execute(
            select(User).where(User.telegram_id == referrer_tg)
        )).scalar_one_or_none()
        referrer_biz = None
        if referrer is not None:
            referrer_biz = (await db.execute(
                select(Business).where(Business.owner_id == referrer.id)
                .order_by(Business.created_at).limit(1)
            )).scalar_one_or_none()

        bonus = settings.etg_referral_bonus

        # The new business always gets its side of the bonus.
        await metering_service.credit(
            business_id=str(new_business_id), amount=bonus,
            description="Referral bonus — welcome!", redis=redis, db=db,
            reference_type="bonus", reference_id=f"ref-in-{referrer_tg}")

        # The referrer gets theirs when they have a business wallet to credit.
        if referrer_biz is not None:
            await metering_service.credit(
                business_id=str(referrer_biz.id), amount=bonus,
                description="Referral bonus — a friend you invited went live",
                redis=redis, db=db,
                reference_type="bonus", reference_id=f"ref-out-{owner_telegram_id}")

        await redis.delete(_key(owner_telegram_id))
        logger.info("Referral redeemed", referred_tg=str(owner_telegram_id),
                    referrer_tg=referrer_tg, bonus=bonus,
                    referrer_credited=referrer_biz is not None)
        return True
    except Exception as exc:
        logger.warning("Referral redemption failed (non-blocking)",
                       error=f"{type(exc).__name__}: {exc}")
        return False
