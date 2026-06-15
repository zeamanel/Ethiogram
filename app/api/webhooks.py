# app/api/webhooks.py
import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    AlertType,
    Bot,
    BotStatus,
    Business,
    BusinessBrainConfig,
    ChatMessage,
    Conversation,
    EtgTransaction,
    MessageRole,
    Platform,
    TokenWallet,
    UsageEvent,
    WalletAlert,
)
from app.db.session import get_db, get_redis
from app.services.telegram_service import MessageEnvelope, telegram_service
from app.core.security import decrypt

logger = get_logger(__name__)

router = APIRouter(tags=["webhooks"])

# ETG costs (fallback if Redis/DB pricing unavailable)
_ETG_COST_BASE_REPLY = 2
_ETG_COST_RAG_SEARCH = 1

# Wallet alert thresholds
_THRESHOLD_LOW = 500
_THRESHOLD_CRITICAL = 100


# ---------------------------------------------------------------------------
# Main webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/webhook/{token_hash}", include_in_schema=False)
async def telegram_webhook(
    token_hash: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Receives every Telegram update for every registered bot.
    ALWAYS returns 200 — Telegram retries on any non-200 response.
    """
    print(f"[WEBHOOK] update received for token_hash={token_hash[:8]}…", flush=True)
    body_bytes = await request.body()

    # 1. Look up bot by token_hash — silent 200 on miss (security: no info leak)
    bot_result = await db.execute(
        select(Bot)
        .where(Bot.token_hash == token_hash)
        .join(Bot.business)
    )
    bot = bot_result.scalar_one_or_none()
    if bot is None:
        logger.warning("Bot not found for token_hash — dropping update",
                       token_hash=token_hash[:12])
        return JSONResponse({"ok": True})

    logger.info(
        "Bot fetched",
        bot_id=str(bot.id),
        business_id=str(bot.business_id),
        bot_status=bot.status.value,
        bot_username=bot.bot_username,
    )

    # 2. Verify Telegram signature
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if bot.webhook_secret and not _verify_signature(body_bytes, bot.webhook_secret, secret_header):
        logger.warning("Webhook signature mismatch — dropping update",
                       bot_id=str(bot.id), token_hash=token_hash[:12],
                       header_present=bool(secret_header))
        return JSONResponse({"ok": True})

    # 3. Parse update
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("Failed to parse webhook JSON — dropping update",
                       bot_id=str(bot.id), error=str(exc))
        return JSONResponse({"ok": True})

    envelope = telegram_service.parse_incoming_update(body, token_hash)
    if envelope is None:
        logger.info("Update has no processable message (envelope=None) — dropping",
                    bot_id=str(bot.id), update_keys=list(body.keys()) if isinstance(body, dict) else None)
        return JSONResponse({"ok": True})

    envelope.business_id = str(bot.business_id)
    envelope.bot_id = str(bot.id)

    logger.info(
        "Webhook update received",
        bot_id=str(bot.id),
        business_id=str(bot.business_id),
        customer_id=envelope.customer_id,
        bot_status=bot.status.value,
        has_text=bool(envelope.text),
    )

    # 4. Suspended bot — silent drop
    if bot.status == BotStatus.suspended:
        logger.info("Dropping update: bot suspended", bot_id=str(bot.id))
        return JSONResponse({"ok": True})

    redis = await get_redis()

    # Decrypt token once for all sends in this request
    try:
        raw_token = decrypt(bot.encrypted_token)
    except Exception as exc:
        logger.error("Token decryption failed", bot_id=str(bot.id), error=str(exc))
        return JSONResponse({"ok": True})

    # 5. Paused bot — inform customer, do not process
    if bot.status in (BotStatus.paused, BotStatus.grace):
        logger.info("Dropping update: bot paused/grace — sending paused message",
                    bot_id=str(bot.id), status=bot.status.value)
        await _send_paused_message(raw_token, envelope)
        return JSONResponse({"ok": True})

    logger.info("Processing update", bot_id=str(bot.id), status=bot.status.value)

    # 6. Balance check (Redis-cached)
    balance = await _get_balance(str(bot.business_id), redis, db)
    logger.info("Balance checked", business_id=str(bot.business_id), balance=balance)
    if balance <= 0:
        logger.info("Zero balance: not processing", business_id=str(bot.business_id), balance=balance)
        await _handle_zero_balance(bot, raw_token, envelope, db, redis)
        return JSONResponse({"ok": True})

    # 7. Typing indicator — fire-and-forget
    await telegram_service.send_typing_action(raw_token, envelope.customer_id)

    # 8. Get or create Conversation
    conversation = await _get_or_create_conversation(envelope, bot, db)

    # 9. Detect language (simple heuristic; full service in later phase)
    language = _detect_language(envelope.text or "")
    if language != conversation.detected_language:
        conversation.detected_language = language

    # 10. Build response via agent (base Q&A until agents layer is built)
    brain_config = await _get_brain_config(str(bot.business_id), db)
    response_text, tokens_used, model_id = await _process_message(
        envelope, conversation, brain_config, db
    )
    logger.info(
        "Reply generated",
        bot_id=str(bot.id),
        model_id=model_id,
        response_chars=len(response_text or ""),
    )

    # 11. Send reply
    try:
        await telegram_service.send_message(raw_token, envelope.customer_id, response_text)
        logger.info(
            "Reply sent to customer",
            bot_id=str(bot.id),
            customer_id=envelope.customer_id,
            model_id=model_id,
        )
    except Exception as exc:
        logger.error("Failed to send reply", bot_id=str(bot.id), error=str(exc), exc_info=True)
        return JSONResponse({"ok": True})

    # 12. Persist chat messages
    await _save_messages(envelope, response_text, model_id, tokens_used, conversation, db)

    # Update conversation stats
    conversation.total_messages += 2
    conversation.last_message_at = datetime.now(timezone.utc)
    bot.total_messages_processed += 1
    bot.last_message_at = datetime.now(timezone.utc)

    # 13. Meter ETG usage
    etg_charged = await _charge_etg(
        str(bot.business_id), str(bot.id), str(conversation.id),
        model_id, tokens_used, balance, redis, db
    )
    conversation.total_etg_spent += etg_charged
    bot.total_etg_consumed += etg_charged

    # 14. Post-charge alert checks
    new_balance = balance - etg_charged
    await _check_wallet_alerts(str(bot.business_id), new_balance, bot, redis, db)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_signature(body: bytes, secret: str, provided: str) -> bool:
    if not provided:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


async def _send_paused_message(token: str, envelope: MessageEnvelope) -> None:
    try:
        await telegram_service.send_message(
            token,
            envelope.customer_id,
            "⚠️ This service is temporarily unavailable. Please try again later.",
        )
    except Exception:
        pass


async def _get_balance(business_id: str, redis, db: AsyncSession) -> int:
    cache_key = f"wallet:balance:{business_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return int(cached)

    result = await db.execute(
        select(TokenWallet.balance).where(TokenWallet.business_id == business_id)
    )
    row = result.scalar_one_or_none()
    balance = int(row) if row is not None else 0

    await redis.setex(cache_key, 60, balance)
    return balance


async def _handle_zero_balance(
    bot: Bot, token: str, envelope: MessageEnvelope, db: AsyncSession, redis
) -> None:
    if bot.status == BotStatus.active:
        bot.status = BotStatus.grace
        bot.grace_period_started_at = datetime.now(timezone.utc)
        db.add(bot)

    try:
        await telegram_service.send_message(
            token,
            envelope.customer_id,
            "⚠️ This service is temporarily unavailable. Please contact the business directly.",
        )
    except Exception:
        pass


async def _get_or_create_conversation(
    envelope: MessageEnvelope, bot: Bot, db: AsyncSession
) -> Conversation:
    result = await db.execute(
        select(Conversation).where(
            Conversation.bot_id == bot.id,
            Conversation.customer_platform_id == envelope.customer_id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        conv = Conversation(
            business_id=bot.business_id,
            bot_id=bot.id,
            platform=Platform.telegram,
            customer_platform_id=envelope.customer_id,
            customer_name=envelope.customer_name,
            customer_username=envelope.customer_username,
            detected_language="en",
            is_active=True,
        )
        db.add(conv)
        await db.flush()
    return conv


async def _get_brain_config(
    business_id: str, db: AsyncSession
) -> BusinessBrainConfig | None:
    result = await db.execute(
        select(BusinessBrainConfig).where(
            BusinessBrainConfig.business_id == business_id
        )
    )
    return result.scalar_one_or_none()


async def _process_message(
    envelope: MessageEnvelope,
    conversation: Conversation,
    brain_config: BusinessBrainConfig | None,
    db: AsyncSession,
) -> tuple[str, dict, str]:
    """
    Route message through AI. Uses BaseAgent when agents layer exists;
    falls back to a direct model call via model_router for now.
    """
    from app.services.model_router import model_router

    persona = brain_config.persona_name if brain_config else "Assistant"
    tone = brain_config.persona_tone if brain_config else "friendly"
    fallback = brain_config.fallback_message if brain_config else "I'm here to help!"

    text = envelope.text or ""
    if not text.strip():
        logger.info("Empty message text — returning fallback without model call",
                    business_id=envelope.business_id)
        return fallback, {"input_tokens": 0, "output_tokens": 0}, "none"

    system_prompt = (
        f"You are {persona}, a {tone} AI assistant for this business. "
        f"Answer customer questions helpfully and concisely. "
        f"If you don't know the answer, say so politely."
    )

    messages = [{"role": "user", "content": text}]

    logger.info("Calling model router", business_id=envelope.business_id, text_chars=len(text))
    try:
        response_text, tokens, model_id = await model_router.execute_with_fallback(
            messages=messages,
            system_prompt=system_prompt,
            business_id=envelope.business_id,
        )
        logger.info("Model router returned reply", business_id=envelope.business_id,
                    model_id=model_id, response_chars=len(response_text or ""))
        return response_text, tokens, model_id
    except Exception as exc:
        logger.error("All models failed — returning fallback text",
                     error=str(exc), business_id=envelope.business_id, exc_info=True)
        return fallback, {"input_tokens": 0, "output_tokens": 0}, "none"


async def _save_messages(
    envelope: MessageEnvelope,
    response_text: str,
    model_id: str,
    tokens: dict,
    conversation: Conversation,
    db: AsyncSession,
) -> None:
    if envelope.text:
        user_msg = ChatMessage(
            conversation_id=conversation.id,
            role=MessageRole.user,
            content=envelope.text,
            media_type=envelope.media_type,
            media_url=envelope.media_url,
            telegram_message_id=envelope.message_id,
        )
        db.add(user_msg)

    assistant_msg = ChatMessage(
        conversation_id=conversation.id,
        role=MessageRole.assistant,
        content=response_text,
        model_used=model_id,
        input_tokens=tokens.get("input_tokens", 0),
        output_tokens=tokens.get("output_tokens", 0),
    )
    db.add(assistant_msg)


async def _charge_etg(
    business_id: str,
    bot_id: str,
    conversation_id: str,
    model_id: str,
    tokens: dict,
    current_balance: int,
    redis,
    db: AsyncSession,
) -> int:
    """Deduct ETG from wallet and write immutable usage records."""
    input_t = tokens.get("input_tokens", 0)
    output_t = tokens.get("output_tokens", 0)
    etg = max(1, (input_t // 1000) + (output_t // 1000) * 2 + _ETG_COST_BASE_REPLY)

    result = await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == business_id)
    )
    wallet = result.scalar_one_or_none()
    if wallet is None:
        return 0

    balance_before = wallet.balance
    wallet.balance = max(0, wallet.balance - etg)
    wallet.lifetime_spent += etg
    wallet.current_month_spend += etg

    tx = EtgTransaction(
        wallet_id=wallet.id,
        amount=-etg,
        balance_before=balance_before,
        balance_after=wallet.balance,
        transaction_type="usage",
        description=f"AI reply ({model_id})",
        reference_type="conversation",
        reference_id=conversation_id,
    )
    db.add(tx)

    usage = UsageEvent(
        business_id=business_id,
        bot_id=bot_id,
        conversation_id=conversation_id,
        action_type="ai_reply",
        model_id=model_id,
        input_tokens=input_t,
        output_tokens=output_t,
        etg_charged=etg,
    )
    db.add(usage)

    # Bust cache
    await redis.setex(f"wallet:balance:{business_id}", 60, wallet.balance)

    logger.etg_charged(
        amount=etg,
        action_type="ai_reply",
        business_id=business_id,
        balance_after=wallet.balance,
    )
    return etg


async def _check_wallet_alerts(
    business_id: str,
    new_balance: int,
    bot: Bot,
    redis,
    db: AsyncSession,
) -> None:
    """Transition bot status and log wallet alerts at threshold crossings."""
    alert_key = f"wallet:alert_sent:{business_id}"
    last_alert = await redis.get(alert_key)

    if new_balance <= 0 and last_alert != "zero":
        await _log_alert(business_id, AlertType.zero, new_balance, db)
        await redis.setex(alert_key, 86400, "zero")
        bot.status = BotStatus.grace
        bot.grace_period_started_at = datetime.now(timezone.utc)

    elif new_balance <= _THRESHOLD_CRITICAL and last_alert not in ("critical", "zero"):
        await _log_alert(business_id, AlertType.critical, new_balance, db)
        await redis.setex(alert_key, 3600, "critical")

    elif new_balance <= _THRESHOLD_LOW and last_alert not in ("low", "critical", "zero"):
        await _log_alert(business_id, AlertType.low, new_balance, db)
        await redis.setex(alert_key, 3600, "low")


async def _log_alert(
    business_id: str, alert_type: AlertType, balance: int, db: AsyncSession
) -> None:
    result = await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == business_id)
    )
    wallet = result.scalar_one_or_none()
    if wallet:
        alert = WalletAlert(
            wallet_id=wallet.id,
            alert_type=alert_type,
            balance_at_alert=balance,
            sent_via=["dashboard"],
        )
        db.add(alert)


def _detect_language(text: str) -> str:
    """
    Lightweight heuristic: detect Amharic by Unicode range U+1200–U+137F.
    Full langdetect library integration deferred to embedding_service phase.
    """
    amharic_chars = sum(1 for c in text if "\u1200" <= c <= "\u137f")
    if amharic_chars > len(text) * 0.3:
        return "am"
    return "en"
