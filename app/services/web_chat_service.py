# app/services/web_chat_service.py
"""Public website support chat.

A stateless AI assistant for the server-rendered landing page (/biz/{slug}): a
visitor types a question and gets an answer grounded in the business's own
knowledge (RAG + catalog + persona), with a nudge to continue on Telegram for
anything transactional (booking/order). No login, no conversation persistence —
the browser keeps the short history and sends it back each turn.

Cost/abuse is bounded by the caller (per-IP + per-business rate limits) and a
low max_tokens here; replies are metered as a zero-rated UsageEvent during the
no-payments phase (flip to real charging by reusing webhooks._reply_cost).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import BusinessBrainConfig
from app.services.model_router import model_router
from app.services.rag_service import rag_service

logger = get_logger(__name__)

_MAX_HISTORY = 6          # turns of client-supplied history we trust
_MAX_MSG_CHARS = 800
_MAX_TOKENS = 300


def _sanitize_history(history) -> list[dict]:
    out: list[dict] = []
    if not isinstance(history, list):
        return out
    for h in history[-_MAX_HISTORY:]:
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content.strip()[:_MAX_MSG_CHARS]})
    return out


async def _build_system_prompt(db: AsyncSession, business, message: str) -> str:
    brain = (await db.execute(
        select(BusinessBrainConfig).where(BusinessBrainConfig.business_id == business.id)
    )).scalar_one_or_none()
    persona = brain.persona_name if brain else "Assistant"
    tone = brain.persona_tone if brain else "friendly"

    parts = [
        f"You are {persona}, a {tone} assistant on the website of \"{business.name}\".",
        "Answer the visitor's questions using ONLY the business information below. "
        "Be concise (2-4 sentences). If you don't know, say so honestly.",
        "For booking an appointment, placing an order, or anything needing personal "
        "details, invite them to continue the chat on Telegram (a button is on the page).",
    ]
    if brain and brain.system_prompt_extra:
        parts.append(brain.system_prompt_extra)

    # RAG over uploaded docs — best-effort (pgvector may be unavailable in tests).
    try:
        chunks = await rag_service.search(
            message, business.id, db,
            top_k=(brain.rag_top_k if brain else 5),
            similarity_threshold=(brain.rag_similarity_threshold if brain else 0.3))
        ctx = rag_service.build_context_string(chunks) if chunks else ""
        if ctx:
            parts.append("=== Reference ===\n" + ctx)
    except Exception as exc:
        logger.info("Web chat RAG skipped", business_id=str(business.id), error=str(exc))

    # Structured catalog (products/services/FAQs) — the heart of support answers.
    try:
        items = await rag_service.get_knowledge_items(business.id, db)
        items_block = rag_service.format_knowledge_items_for_prompt(items)
        if items_block:
            parts.append(items_block)
    except Exception:
        pass

    if business.address:
        parts.append(f"Location: {business.address}")
    return "\n\n".join(parts)


async def answer(db: AsyncSession, business, message: str, history) -> dict:
    """Generate a website-chat reply. Returns {reply, source, model}. Never raises
    on an LLM failure — falls back to a safe handoff message."""
    message = (message or "").strip()[:_MAX_MSG_CHARS]
    if not message:
        return {"reply": "Please type a question and I'll help.", "source": "empty", "model": None}

    system = await _build_system_prompt(db, business, message)
    messages = _sanitize_history(history) + [{"role": "user", "content": message}]
    # Amharic (Ge'ez script) → route to the Gemini chain like the bot does.
    language = "am" if any("ሀ" <= c <= "፿" for c in message) else None

    try:
        text, _tokens, model_used = await model_router.execute_with_fallback(
            messages=messages, system_prompt=system, business_id=business.id,
            max_tokens=_MAX_TOKENS, temperature=0.4, language=language)
        reply = (text or "").strip()
        if reply:
            return {"reply": reply, "source": "ai", "model": model_used}
    except Exception as exc:
        logger.warning("Web chat LLM call failed", business_id=str(business.id),
                       error=f"{type(exc).__name__}: {exc}")

    return {"reply": "Sorry, I couldn't answer that right now — please continue the "
                     "chat on Telegram and we'll help you out.", "source": "fallback", "model": None}
