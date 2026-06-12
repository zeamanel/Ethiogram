# app/api/bots.py
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.core.security import decrypt
from app.db.models import Bot, BotStatus, Business
from app.db.session import get_db, get_redis
from app.services.bot_onboarding import offboard_bot, onboard_bot

logger = get_logger(__name__)
router = APIRouter(prefix="/bots", tags=["bots"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class OnboardRequest(BaseModel):
    token: str
    business_id: uuid.UUID


class BotResponse(BaseModel):
    id: str
    business_id: str
    bot_username: Optional[str]
    bot_display_name: Optional[str]
    platform: str
    status: str
    webhook_url: Optional[str]
    total_messages_processed: int
    total_etg_consumed: int
    last_message_at: Optional[str]
    created_at: str


class BotStatusUpdate(BaseModel):
    status: str

    def validated_status(self) -> BotStatus:
        allowed = {BotStatus.active.value, BotStatus.paused.value}
        if self.status not in allowed:
            raise ValidationError(f"Owners can only set status to: {allowed}")
        return BotStatus(self.status)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=BotResponse, status_code=201)
async def onboard_new_bot(
    body: OnboardRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> BotResponse:
    """Validate token, register webhook, create bot record."""
    await _assert_owns_business(current_user.id, body.business_id, db)

    bot = await onboard_bot(
        token=body.token,
        business_id=body.business_id,
        owner_id=current_user.id,
        db=db,
        redis=redis,
    )
    return _bot_to_response(bot)


@router.get("", response_model=list[BotResponse])
async def list_bots(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    business_id: Optional[uuid.UUID] = Query(None),
) -> list[BotResponse]:
    stmt = (
        select(Bot)
        .join(Bot.business)
        .where(Business.owner_id == current_user.id)
    )
    if business_id:
        stmt = stmt.where(Bot.business_id == business_id)
    stmt = stmt.order_by(Bot.created_at.desc())

    result = await db.execute(stmt)
    bots = result.scalars().all()
    return [_bot_to_response(b) for b in bots]


@router.get("/{bot_id}", response_model=BotResponse)
async def get_bot(
    bot_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    bot = await _get_owned_bot(bot_id, current_user.id, db)
    return _bot_to_response(bot)


@router.patch("/{bot_id}/status", response_model=BotResponse)
async def update_bot_status(
    bot_id: uuid.UUID,
    body: BotStatusUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    """Owners can pause or resume their bots."""
    bot = await _get_owned_bot(bot_id, current_user.id, db)
    bot.status = body.validated_status()
    db.add(bot)
    logger.info("Bot status updated", bot_id=str(bot_id), status=bot.status.value)
    return _bot_to_response(bot)


@router.delete("/{bot_id}", status_code=204)
async def disconnect_bot(
    bot_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove webhook and mark bot as disconnected. Record is kept for history."""
    bot = await _get_owned_bot(bot_id, current_user.id, db)

    raw_token = decrypt(bot.encrypted_token)
    await offboard_bot(bot, raw_token, db)


@router.post("/{bot_id}/test", response_model=dict)
async def test_bot(
    bot_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send a test ping to the bot to verify it's live."""
    bot = await _get_owned_bot(bot_id, current_user.id, db)

    raw_token = decrypt(bot.encrypted_token)
    from app.services.telegram_service import telegram_service
    try:
        info = await telegram_service.get_me(raw_token)
        return {
            "ok": True,
            "bot_username": info.get("username"),
            "bot_name": info.get("first_name"),
            "webhook_url": bot.webhook_url,
            "status": bot.status.value,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/{bot_id}/refresh-webhook", response_model=dict)
async def refresh_webhook(
    bot_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-register the webhook (useful if it drifts or gets deleted externally)."""
    bot = await _get_owned_bot(bot_id, current_user.id, db)

    raw_token = decrypt(bot.encrypted_token)
    from app.services.telegram_service import telegram_service
    from app.core.security import generate_webhook_secret

    new_secret = generate_webhook_secret()
    await telegram_service.set_webhook(raw_token, bot.webhook_url, new_secret)
    bot.webhook_secret = new_secret
    db.add(bot)

    return {"ok": True, "webhook_url": bot.webhook_url}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_owned_bot(
    bot_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession
) -> Bot:
    result = await db.execute(
        select(Bot)
        .join(Bot.business)
        .where(Bot.id == bot_id, Business.owner_id == user_id)
    )
    bot = result.scalar_one_or_none()
    if bot is None:
        raise NotFoundError("Bot", str(bot_id))
    return bot


async def _assert_owns_business(
    user_id: uuid.UUID, business_id: uuid.UUID, db: AsyncSession
) -> None:
    result = await db.execute(
        select(Business.id).where(
            Business.id == business_id,
            Business.owner_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Business", str(business_id))


def _bot_to_response(bot: Bot) -> BotResponse:
    return BotResponse(
        id=str(bot.id),
        business_id=str(bot.business_id),
        bot_username=bot.bot_username,
        bot_display_name=bot.bot_display_name,
        platform=bot.platform.value,
        status=bot.status.value,
        webhook_url=bot.webhook_url,
        total_messages_processed=bot.total_messages_processed,
        total_etg_consumed=bot.total_etg_consumed,
        last_message_at=bot.last_message_at.isoformat() if bot.last_message_at else None,
        created_at=bot.created_at.isoformat(),
    )
