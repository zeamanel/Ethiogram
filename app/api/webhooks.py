# app/api/webhooks.py
import hmac
import json
import secrets as _secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    Agent,
    AlertType,
    Bot,
    BotStatus,
    Business,
    BusinessBrainConfig,
    ChatMessage,
    ChildAgent,
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
from app.core.security import decrypt, decrypt_agent_prompt, decrypt_child_secrets

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
    print(f"[WEBHOOK] body read ({len(body_bytes)} bytes); looking up bot…", flush=True)
    logger.info("Looking up bot by token_hash", token_hash=token_hash[:12])

    # 1. Look up bot by token_hash — silent 200 on miss (security: no info leak)
    try:
        bot_result = await db.execute(
            select(Bot)
            .where(Bot.token_hash == token_hash)
            .join(Bot.business)
            # Suspended/deleted business → no bot match → silent drop below (the
            # bot stops responding, IO-free; no lazy load of bot.business).
            .where(Business.is_suspended.is_(False), Business.deleted_at.is_(None))
        )
        bot = bot_result.scalar_one_or_none()
    except Exception as exc:
        print(f"[WEBHOOK] bot lookup FAILED: {type(exc).__name__}: {exc}", flush=True)
        logger.error("Bot lookup query failed", token_hash=token_hash[:12],
                     error=f"{type(exc).__name__}: {exc}", exc_info=True)
        return JSONResponse({"ok": True})

    print(f"[WEBHOOK] bot lookup done: found={bot is not None}", flush=True)
    if bot is None:
        logger.warning("Bot not found for token_hash — dropping update",
                       token_hash=token_hash[:12])
        return JSONResponse({"ok": True})

    # Capture identifiers as plain strings up front. After a rollback in the
    # post-reply error handler, the ORM objects are expired, so touching
    # bot.id/business_id there would trigger a lazy reload — which in async mode
    # raises MissingGreenlet and turns a swallowed failure into a 500 (-> Telegram
    # retry). Using this local keeps the handler IO-free.
    bot_id_str = str(bot.id)

    logger.info(
        "Bot fetched",
        bot_id=str(bot.id),
        business_id=str(bot.business_id),
        bot_status=bot.status.value,
        bot_username=bot.bot_username,
    )

    # 2. Verify Telegram signature
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if bot.webhook_secret and not _verify_signature(bot.webhook_secret, secret_header):
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

    # Idempotency: Telegram resends the same update_id on every retry. Accept
    # each update at most once, so a downstream error can never re-invoke the
    # (paid) model call for the same message.
    update_id = body.get("update_id")
    if update_id is not None:
        dedup_key = f"tg:update:{bot.id}:{update_id}"
        if not await redis.set(dedup_key, "1", nx=True, ex=3600):
            logger.info("Duplicate update_id — already processed, skipping",
                        bot_id=str(bot.id), update_id=update_id)
            return JSONResponse({"ok": True})

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

    # Load the business's billing policy (who pays per message).
    business = await db.get(Business, bot.business_id)
    business_pays_cost = (business is None) or business.billing_policy in ("business_pays", "both")

    # 6. Balance check (Redis-cached). Only gates when the BUSINESS pays the
    # platform cost; in user_pays the business wallet isn't used for messages.
    balance = await _get_balance(str(bot.business_id), redis, db)
    logger.info("Balance checked", business_id=str(bot.business_id), balance=balance)
    if business_pays_cost and balance <= 0:
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

    # 9b0. /balance command — let the customer check their balance for free.
    if business is not None and (envelope.text or "").strip().lower() in ("/balance", "balance"):
        await telegram_service.send_message(
            raw_token, envelope.customer_id, _balance_message(business, conversation))
        return JSONResponse({"ok": True})

    # 9c. Per-user billing precheck — decide who pays this message and enforce
    # the free-tier cap / user balance before spending on a model call.
    payer = "business"
    if business is not None:
        _apply_monthly_reset(conversation, datetime.now(timezone.utc))
        payer, block = _decide_payer(business, conversation)
        if block == "recharge":
            await telegram_service.send_message(
                raw_token, envelope.customer_id, _recharge_message(business, conversation))
            return JSONResponse({"ok": True})
        if block == "limit":
            await telegram_service.send_message(
                raw_token, envelope.customer_id,
                "You've reached this month's free message limit. Please try again next month.")
            return JSONResponse({"ok": True})

    # 9b. Booking sub-flow (Concierge with a configured calendar): present real
    # slots on booking intent, or confirm a tapped slot. Handles its own reply.
    if await _maybe_handle_booking(envelope, bot, raw_token, conversation, db, redis, body):
        return JSONResponse({"ok": True})

    # 10. Build response: classify intent → route to the right agent
    brain_config = await _get_brain_config(str(bot.business_id), db)
    response_text, tokens_used, model_id = await _process_message(
        envelope, conversation, brain_config, db, raw_token
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

    # 12-14. Persist, update stats, meter ETG, and check alerts.
    # These run AFTER the reply has been delivered, so a failure here must NOT
    # propagate: a non-200 response makes Telegram retry the update and
    # re-invoke the paid model call. Roll back to leave the session clean
    # (get_db commits on return) and still return 200.
    try:
        await _save_messages(envelope, response_text, model_id, tokens_used, conversation, db)

        # Update conversation stats
        conversation.total_messages += 2
        conversation.last_message_at = datetime.now(timezone.utc)
        bot.total_messages_processed += 1
        bot.last_message_at = datetime.now(timezone.utc)

        # 13. Meter ETG usage per the billing policy (business/user/both pay).
        etg_charged = await _charge_etg(
            business, conversation, str(bot.id), model_id, tokens_used, payer, redis, db
        )
        conversation.total_etg_spent += etg_charged
        bot.total_etg_consumed += etg_charged

        # 14. Post-charge alert checks (re-read the actual business balance)
        new_balance = await _get_balance(str(bot.business_id), redis, db)
        await _check_wallet_alerts(str(bot.business_id), new_balance, bot, redis, db)
    except Exception as exc:
        await db.rollback()
        logger.error(
            "Post-reply persistence/metering failed (reply already sent) — returning 200",
            bot_id=bot_id_str, error=f"{type(exc).__name__}: {exc}", exc_info=True,
        )

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_signature(secret: str, provided: str) -> bool:
    """
    Telegram echoes the ``secret_token`` set via setWebhook *verbatim* in the
    ``X-Telegram-Bot-Api-Secret-Token`` header — it is NOT an HMAC of the body.
    Verify with a constant-time comparison of the header against the stored secret.
    """
    if not provided:
        return False
    return hmac.compare_digest(secret, provided)


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


# Map a marketplace father Agent to the local agent class that implements it,
# by keyword on its category / tags / capabilities.
_ACCOUNTANT_KEYWORDS = ("account", "receipt", "expense", "finance", "invoice", "bookkeep")
_CONCIERGE_KEYWORDS = ("concierge", "booking", "appointment", "schedule", "reserv", "calendar")


def _agent_type_for(agent: Agent) -> str:
    """Classify a father Agent into the local agent class name that runs it."""
    hay = " ".join([
        agent.category or "",
        " ".join(agent.tags or []),
        " ".join(agent.capabilities or []),
    ]).lower()
    if any(k in hay for k in _ACCOUNTANT_KEYWORDS):
        return "AccountantAgent"
    if any(k in hay for k in _CONCIERGE_KEYWORDS):
        return "ConciergeAgent"
    return "BaseAgent"


async def _load_child_data(business_id: str, agent_type: str, db: AsyncSession) -> dict | None:
    """
    Return the business's active ChildAgent config for ``agent_type``, enriched
    with the decrypted father system prompt under ``_father_prompt``.

    This is what makes specialists business-specific: the Concierge gets the
    owner's real services/hours/timezone (and calendar config), the Accountant
    gets its business context, etc. Returns None when the business has no
    matching deployed agent — the agent then uses its generic behaviour.
    """
    from uuid import UUID
    bid = business_id if isinstance(business_id, UUID) else UUID(str(business_id))
    result = await db.execute(
        select(ChildAgent, Agent)
        .join(Agent, ChildAgent.agent_id == Agent.id)
        .where(ChildAgent.business_id == bid, ChildAgent.is_active.is_(True))
    )
    for child, father in result.all():
        if _agent_type_for(father) != agent_type:
            continue
        # Plain config (rendered into the prompt). Keys are owner-controlled, so
        # strip any "_"-prefixed keys to avoid clobbering reserved slots.
        data = {k: v for k, v in (child.child_data or {}).items() if not k.startswith("_")}
        try:
            data["_father_prompt"] = decrypt_agent_prompt(
                father.encrypted_system_prompt, father.encryption_key_ref
            )
        except Exception as exc:
            logger.warning("Failed to decrypt father prompt",
                           agent_id=str(father.id), error=str(exc))
        # Sensitive config (credentials/API keys): decrypted only here and placed
        # under "_secrets" — a "_"-prefixed key, so build_system_prompt never
        # renders it into the model prompt. Agent code reads it directly.
        if child.child_secrets:
            try:
                data["_secrets"] = decrypt_child_secrets(child.child_secrets)
            except Exception as exc:
                logger.error("Failed to decrypt child_secrets",
                             child_agent_id=str(child.id), error=str(exc))
        # The father agent's admin-set model (Agent.preferred_model_id) — read
        # fresh per message, so an admin change takes effect on the next reply.
        data["_father_model_id"] = father.preferred_model_id
        return data
    return None


async def _maybe_handle_booking(
    envelope: MessageEnvelope,
    bot: Bot,
    raw_token: str,
    conversation: Conversation,
    db: AsyncSession,
    redis,
    body: dict,
) -> bool:
    """
    Handle the Concierge booking sub-flow. Returns True if it produced the reply
    (and the caller should stop), False to fall through to the normal agent path.

    - callback_query "book_slot:<key>:<idx>" → confirm via real calendar.
    - booking intent + a Concierge configured with calendar credentials →
      fetch REAL availability and present slot buttons.
    """
    from app.agents.concierge import concierge_agent
    from app.agents.router import Intent, intent_router

    cb = body.get("callback_query")

    # A. A tapped slot → confirm the booking.
    if cb and str(cb.get("data", "")).startswith("book_slot:"):
        await _confirm_booking_callback(envelope, bot, raw_token, conversation, db, redis, cb)
        return True
    if cb:
        return False  # some other callback — let the normal flow deal with it

    # B. Booking intent → present real slots, only if a Concierge is configured.
    if intent_router.classify(envelope) != Intent.BOOKING:
        return False
    child_data = await _load_child_data(str(conversation.business_id), "ConciergeAgent", db)
    if not child_data or not (child_data.get("_secrets") or {}).get("credentials_json"):
        return False  # no configured calendar → fall through to LLM booking guidance

    target_day = datetime.now(timezone.utc) + timedelta(days=1)
    slots = await concierge_agent.get_available_slots(child_data, target_day)
    if not slots:
        await telegram_service.send_message(
            raw_token, envelope.customer_id,
            "I don't see any open times right now. Please try again later or contact us directly.",
        )
        return True

    session_key = _secrets.token_hex(6)
    await redis.set(f"book:{bot.id}:{session_key}", json.dumps(slots), ex=3600)
    buttons = concierge_agent.format_slots_as_buttons(slots, session_key)
    await telegram_service.send_message_with_buttons(
        raw_token, envelope.customer_id,
        "Here are the next available times — tap one to book:", buttons,
    )
    logger.info("Presented booking slots", bot_id=str(bot.id), slots=len(slots))
    return True


async def _confirm_booking_callback(
    envelope: MessageEnvelope,
    bot: Bot,
    raw_token: str,
    conversation: Conversation,
    db: AsyncSession,
    redis,
    cb: dict,
) -> None:
    """Confirm a tapped slot against the real calendar and reply honestly."""
    from app.agents.concierge import concierge_agent

    # Always answer the callback so the client's spinner stops.
    try:
        await telegram_service.answer_callback_query(raw_token, cb.get("id"))
    except Exception:
        pass

    parts = str(cb.get("data", "")).split(":")
    if len(parts) != 3:
        return
    _, session_key, idx_s = parts

    raw = await redis.get(f"book:{bot.id}:{session_key}")
    if not raw:
        await telegram_service.send_message(
            raw_token, envelope.customer_id,
            "⚠️ That booking option expired. Please ask for available times again.",
        )
        return
    try:
        slots = json.loads(raw)
        slot = slots[int(idx_s)]
    except (ValueError, IndexError, TypeError, json.JSONDecodeError):
        await telegram_service.send_message(
            raw_token, envelope.customer_id,
            "⚠️ I couldn't read that slot. Please ask for available times again.",
        )
        return

    child_data = await _load_child_data(str(conversation.business_id), "ConciergeAgent", db)
    result = await concierge_agent.confirm_booking(
        child_data=child_data,
        slot_start=slot["start"],
        slot_end=slot["end"],
        customer_name=envelope.customer_name or "Customer",
        customer_email=None,
        service_name=(child_data or {}).get("services") if child_data else None,
    )
    # booking_reply_text is the single source of truth for what the customer is
    # told — it says "confirmed" ONLY when the calendar write actually succeeded.
    await telegram_service.send_message(
        raw_token, envelope.customer_id,
        concierge_agent.booking_reply_text(result, slot_label=slot.get("label")),
    )
    # On success, consume the slot session so a re-tap can't double-book.
    if result.get("status") != "failed" and result.get("id"):
        await redis.delete(f"book:{bot.id}:{session_key}")
        logger.info("Booking confirmed", bot_id=str(bot.id), event_id=result.get("id"))
    else:
        logger.warning("Booking failed", bot_id=str(bot.id), error=result.get("error"))


async def _process_message(
    envelope: MessageEnvelope,
    conversation: Conversation,
    brain_config: BusinessBrainConfig | None,
    db: AsyncSession,
    raw_token: str | None = None,
) -> tuple[str, dict, str]:
    """
    Classify the message intent, route it to the right agent, and run it.

    The selected agent (Accountant for receipts, Concierge for bookings, or the
    base Q&A agent for everything else) handles RAG + model failover internally.
    If agent processing raises for any reason, fall back to a direct model call
    so the bot never goes silent.
    """
    from app.agents.router import intent_router
    from app.services.model_router import model_router

    fallback = brain_config.fallback_message if brain_config else "I'm here to help!"
    text = envelope.text or ""
    has_media = envelope.media_type in ("photo", "document")

    # Nothing to act on (no text and no media) → cheap fallback, no model call.
    if not text.strip() and not has_media:
        logger.info("Empty message — returning fallback without model call",
                    business_id=envelope.business_id)
        return fallback, {"input_tokens": 0, "output_tokens": 0}, "none"

    # Agents that download media (Accountant OCR) need the raw bot token; it is
    # passed out-of-band on the envelope's raw payload.
    if raw_token:
        envelope.raw["_bot_token"] = raw_token

    # 1. Classify intent → 2. select the agent instance for it.
    intent = intent_router.classify(envelope)
    agent = intent_router.select_agent(intent)

    # 2b. Load the business's deployed config for this agent type (ChildAgent),
    # so specialists answer with the owner's real services/hours/timezone and
    # the father agent's prompt instead of generic defaults.
    child_data = None
    if conversation is not None and db is not None:
        child_data = await _load_child_data(
            str(conversation.business_id), type(agent).__name__, db
        )

    logger.info("Routing message to agent", intent=intent, agent=agent.agent_name,
                business_id=envelope.business_id, child_data=bool(child_data))

    # 3. Run the agent (RAG + model failover happen inside process()). The
    # father agent's admin-set model (if any) is applied here.
    agent_model_id = (child_data or {}).get("_father_model_id")
    try:
        result = await agent.process(
            envelope=envelope,
            conversation=conversation,
            brain_config=brain_config,
            child_data=child_data,
            db=db,
            agent_model_id=agent_model_id,
        )
        logger.info("Agent returned reply", business_id=envelope.business_id,
                    agent=agent.agent_name, model_id=result.model_id,
                    response_chars=len(result.text or ""))
        tokens = {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
        return result.text, tokens, result.model_id
    except Exception as exc:
        logger.error("Agent processing failed — falling back to direct model call",
                     agent=agent.agent_name, business_id=envelope.business_id,
                     error=f"{type(exc).__name__}: {exc}", exc_info=True)

    # 4. Fallback path: a plain model call so the bot never goes silent.
    persona = brain_config.persona_name if brain_config else "Assistant"
    tone = brain_config.persona_tone if brain_config else "friendly"
    system_prompt = (
        f"You are {persona}, a {tone} AI assistant for this business. "
        f"Answer customer questions helpfully and concisely. "
        f"If you don't know the answer, say so politely."
    )
    try:
        response_text, tokens, model_id = await model_router.execute_with_fallback(
            messages=[{"role": "user", "content": text}],
            system_prompt=system_prompt,
            business_id=envelope.business_id,
        )
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


def _apply_monthly_reset(conversation, now) -> None:
    """Roll the per-user free-tier counter at the start of each calendar month."""
    r = conversation.monthly_reset_at
    if r is None or (r.year, r.month) != (now.year, now.month):
        conversation.monthly_etg_used = 0
        conversation.monthly_reset_at = now


def _decide_payer(business, conversation) -> tuple[str, "str | None"]:
    """Return (payer, block_reason). payer ∈ {business,user,both}. A non-None
    block_reason ('recharge' | 'limit') means the message must not be processed."""
    policy = business.billing_policy
    price = business.service_price or 0
    bal = conversation.etg_balance or 0

    if policy == "user_pays":
        return ("user", None) if bal >= price else ("user", "recharge")
    if policy == "both":
        return ("both", None) if bal >= price else ("both", "recharge")

    # business_pays — enforce the optional free-tier monthly cap per user.
    limit = business.per_user_monthly_limit
    used = conversation.monthly_etg_used or 0
    if limit is not None and used >= limit:
        if business.per_user_limit_action == "user_pays":     # switch this user to user-pays
            return ("user", None) if bal >= price else ("user", "recharge")
        return ("business", "limit")                          # block
    return ("business", None)


def _balance_message(business, conversation) -> str:
    """Customer-facing balance summary (for /balance)."""
    if business.billing_policy not in ("user_pays", "both"):
        return "Good news — this business covers the cost of your messages. No balance needed. 🎉"
    bal = conversation.etg_balance or 0
    price = business.service_price or 0
    line = f"Your balance: {bal} ETG."
    if price:
        line += f" Each reply costs {price} ETG."
    return line + " To top up, contact this business and they'll add credit to your balance."


def _recharge_message(business, conversation) -> str:
    bal = conversation.etg_balance or 0
    price = business.service_price or 0
    return (f"You're out of balance for this service (you have {bal} ETG"
            + (f", each reply costs {price} ETG" if price else "")
            + "). Please contact this business to top up, then send your message again. "
              "Type /balance any time to check.")


async def _charge_etg(
    business,
    conversation,
    bot_id: str,
    model_id: str,
    tokens: dict,
    payer: str,
    redis,
    db: AsyncSession,
) -> int:
    """Meter ETG per the billing policy and write immutable usage records.
    Returns the platform cost (for stats). Business pays the cost; when the user
    pays, their per-business balance is debited the service price and the
    business wallet is credited the markup."""
    if business is None:
        return 0
    input_t = tokens.get("input_tokens", 0)
    output_t = tokens.get("output_tokens", 0)
    cost = max(1, (input_t // 1000) + (output_t // 1000) * 2 + _ETG_COST_BASE_REPLY)
    business_id = str(business.id)
    conversation_id = str(conversation.id)

    wallet = (await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == business.id)
    )).scalar_one_or_none()

    # Free-tier counter: only business-subsidised usage counts toward the cap.
    if payer in ("business", "both"):
        conversation.monthly_etg_used = (conversation.monthly_etg_used or 0) + cost

    # Business pays the platform cost.
    if payer in ("business", "both") and wallet is not None:
        before = wallet.balance
        wallet.balance = max(0, wallet.balance - cost)
        wallet.lifetime_spent += cost
        wallet.current_month_spend += cost
        db.add(EtgTransaction(
            wallet_id=wallet.id, amount=-cost, balance_before=before, balance_after=wallet.balance,
            transaction_type="usage", description=f"AI reply ({model_id})",
            reference_type="conversation", reference_id=conversation_id,
        ))

    # User pays the service price; the business earns the markup as profit.
    if payer in ("user", "both"):
        conversation.etg_balance = (conversation.etg_balance or 0) - (business.service_price or 0)
        if wallet is not None and business.business_markup:
            before = wallet.balance
            wallet.balance += business.business_markup
            db.add(EtgTransaction(
                wallet_id=wallet.id, amount=business.business_markup,
                balance_before=before, balance_after=wallet.balance,
                transaction_type="markup", description="User-paid message markup",
                reference_type="conversation", reference_id=conversation_id,
            ))

    db.add(UsageEvent(
        business_id=business.id, bot_id=uuid.UUID(bot_id), conversation_id=conversation.id,
        action_type="ai_reply", model_id=model_id,
        input_tokens=input_t, output_tokens=output_t, etg_charged=cost, payer=payer,
    ))

    if wallet is not None:
        await redis.setex(f"wallet:balance:{business_id}", 60, wallet.balance)
    logger.etg_charged(amount=cost, action_type="ai_reply", business_id=business_id,
                       balance_after=(wallet.balance if wallet else 0))
    return cost


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
