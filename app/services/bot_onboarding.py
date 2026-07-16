# app/services/bot_onboarding.py
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    BotTokenAlreadyRegisteredError,
    BotTokenInvalidError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.security import encrypt, generate_webhook_secret, hash_bot_token
from app.db.models import (
    Bot,
    Business,
    BusinessBrainConfig,
    BotStatus,
    EtgTransaction,
    Platform,
    TokenWallet,
)
from app.services.telegram_service import telegram_service

logger = get_logger(__name__)


async def onboard_bot(
    token: str,
    business_id: uuid.UUID,
    owner_id: uuid.UUID,
    db: AsyncSession,
    redis,
) -> Bot:
    """
    Full bot registration flow:
      1. Validate token with Telegram (/getMe)
      2. Ensure token not already registered
      3. Encrypt + hash token, persist Bot record
      4. Register webhook with Telegram
      5. Ensure BusinessBrainConfig exists
      6. Credit ETG new-user bonus on first bot
      7. Send welcome message to owner
    Returns the newly created Bot ORM object.
    """

    # 1. Validate token
    try:
        bot_info = await telegram_service.get_me(token)
    except Exception as exc:
        logger.warning("Bot token validation failed", error=str(exc))
        raise BotTokenInvalidError()

    bot_username: str = bot_info.get("username", "")
    bot_display_name: str = bot_info.get("first_name", bot_username)

    # 2. Duplicate-token check
    token_hash = hash_bot_token(token)
    existing = await db.execute(select(Bot).where(Bot.token_hash == token_hash))
    if existing.scalar_one_or_none() is not None:
        raise BotTokenAlreadyRegisteredError()

    # 3. Verify business belongs to owner
    biz_result = await db.execute(
        select(Business).where(Business.id == business_id, Business.owner_id == owner_id)
    )
    business = biz_result.scalar_one_or_none()
    if business is None:
        raise NotFoundError("Business", str(business_id))

    # 4. Persist Bot record
    encrypted_token = encrypt(token)
    webhook_secret = generate_webhook_secret()
    webhook_url = f"{settings.webhook_base_url}/{token_hash}"

    bot = Bot(
        business_id=business_id,
        bot_username=bot_username,
        bot_display_name=bot_display_name,
        encrypted_token=encrypted_token,
        token_hash=token_hash,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        platform=Platform.telegram,
        status=BotStatus.active,
    )
    db.add(bot)
    await db.flush()  # get bot.id before webhook registration

    # 5. Register webhook with Telegram
    try:
        await telegram_service.set_webhook(token, webhook_url, webhook_secret)
    except Exception as exc:
        logger.error("Webhook registration failed", bot_id=str(bot.id), error=str(exc))
        # Bot record is not yet committed — rollback happens naturally on exception
        raise

    logger.info(
        "Webhook registered",
        action="bot_onboarded",
        bot_id=str(bot.id),
        bot_username=bot_username,
        business_id=str(business_id),
        webhook_url=webhook_url,
    )

    # 6. Ensure BusinessBrainConfig exists (idempotent)
    brain_result = await db.execute(
        select(BusinessBrainConfig).where(BusinessBrainConfig.business_id == business_id)
    )
    if brain_result.scalar_one_or_none() is None:
        brain_config = BusinessBrainConfig(
            business_id=business_id,
            persona_name=bot_display_name,
            persona_tone="friendly",
            rag_top_k=settings.rag_default_top_k,
            rag_similarity_threshold=settings.rag_default_similarity_threshold,
            max_history_messages=settings.max_conversation_history,
        )
        db.add(brain_config)

    # 7. New-user ETG bonus on first bot for this business
    wallet_result = await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == business_id)
    )
    wallet = wallet_result.scalar_one_or_none()

    if wallet is None:
        wallet = TokenWallet(
            business_id=business_id,
            balance=settings.etg_new_user_bonus,
            lifetime_recharged=settings.etg_new_user_bonus,
        )
        db.add(wallet)
        await db.flush()

        bonus_tx = EtgTransaction(
            wallet_id=wallet.id,
            amount=settings.etg_new_user_bonus,
            balance_before=0,
            balance_after=settings.etg_new_user_bonus,
            transaction_type="bonus",
            description="New user welcome bonus",
            reference_type="onboarding",
            reference_id=str(bot.id),
        )
        db.add(bonus_tx)

        # Invalidate cached balance so next read hits DB
        await redis.delete(f"wallet:balance:{business_id}")

        logger.etg_charged(
            amount=-settings.etg_new_user_bonus,  # negative = credit
            action_type="new_user_bonus",
            business_id=str(business_id),
            balance_after=settings.etg_new_user_bonus,
        )

    # 8. Send welcome message to owner via the new bot
    # owner's Telegram chat_id is stored on their User record; we use
    # a best-effort send so it never blocks onboarding if it fails.
    try:
        owner_result = await db.execute(
            select(Business).where(Business.id == business_id)
        )
        biz = owner_result.scalar_one_or_none()
        if biz and biz.owner:
            owner_tg_id = biz.owner.telegram_id
            if owner_tg_id:
                # First bot went live = the referral activation moment. Credits
                # both sides if this owner arrived through a referral link.
                from app.services import referral_service
                await referral_service.redeem(db, redis, business_id, owner_tg_id)
                await _send_welcome_message(token, owner_tg_id, bot_display_name, business_id)
    except Exception as exc:
        logger.warning("Welcome message failed (non-blocking)", error=str(exc))

    return bot


async def _send_welcome_message(
    token: str,
    owner_telegram_id: int,
    bot_name: str,
    business_id: uuid.UUID,
) -> None:
    dashboard_url = f"{settings.dashboard_url}/bots"
    text = (
        f"<b>Your bot is live!</b>\n\n"
        f"<b>{bot_name}</b> is now connected to Ethiogram and ready to receive messages.\n\n"
        f"Next steps:\n"
        f"• Upload your menu or product catalog to train the AI\n"
        f"• Customize your bot's persona and tone\n"
        f"• Test your bot by sending it a message\n\n"
        f"<a href=\"{dashboard_url}\">Open Dashboard</a>"
    )
    await telegram_service.send_message(token, owner_telegram_id, text)


async def offboard_bot(
    bot: Bot,
    token_decrypted: str,
    db: AsyncSession,
) -> None:
    """
    Remove a bot: delete Telegram webhook and mark as disconnected.
    The Bot record is kept for audit/history; soft delete via status change.
    """
    try:
        await telegram_service.delete_webhook(token_decrypted, drop_pending=False)
    except Exception as exc:
        logger.warning("Failed to delete webhook during offboarding", error=str(exc))

    bot.status = BotStatus.disconnected
    bot.webhook_url = None
    bot.webhook_secret = None
    db.add(bot)

    logger.info(
        "Bot offboarded",
        action="bot_offboarded",
        bot_id=str(bot.id),
        business_id=str(bot.business_id),
    )
