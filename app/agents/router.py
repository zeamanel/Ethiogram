# app/agents/router.py
from __future__ import annotations

import re
from typing import Optional

from app.agents.accountant import accountant_agent
from app.agents.base import BaseAgent, base_agent
from app.agents.concierge import concierge_agent
from app.agents.group import group_agent
from app.core.logging import get_logger
from app.services.telegram_service import MessageEnvelope

logger = get_logger(__name__)


class Intent:
    GREETING = "greeting"
    PRODUCT_INQUIRY = "product_inquiry"
    ORDER = "order"
    BOOKING = "booking"
    RECEIPT_OCR = "receipt_ocr"
    PRICE_CHECK = "price_check"
    COMPLAINT = "complaint"
    HUMAN_HANDOFF = "human_handoff"
    GENERAL_QA = "general_qa"


# ---------------------------------------------------------------------------
# Keyword banks (English + Amharic transliterations)
# ---------------------------------------------------------------------------

_GREETING_WORDS = {
    "hello", "hi", "hey", "selam", "salam", "ሰላም", "ሃሎ",
    "good morning", "good evening", "good afternoon",
}

_ORDER_WORDS = {
    "order", "buy", "purchase", "ዕዝ", "ግዢ", "ልዝ", "add to cart", "checkout",
    "i want", "i need", "i'd like", "i would like",
}

_BOOKING_WORDS = {
    "book", "appointment", "schedule", "reserve", "reservation",
    "ቀጠሮ", "ቀጠሮ ያዝ", "slot", "available", "when can i",
}

_PRICE_WORDS = {
    "price", "cost", "how much", "ዋጋ", "ምን ያህል", "birr", "ብር",
    "rate", "fee", "charge",
}

_COMPLAINT_WORDS = {
    "problem", "issue", "not working", "broken", "complaint", "wrong",
    "bad", "terrible", "disappointed", "refund", "return",
}

_HANDOFF_WORDS = {
    "human", "agent", "person", "staff", "manager", "speak to",
    "talk to", "real person", "support",
}


# Registry: maps an agent's class name to its live singleton instance, so the
# caller can go straight from a routing decision to an object with .process().
_AGENT_REGISTRY: dict[str, BaseAgent] = {
    "BaseAgent": base_agent,
    "AccountantAgent": accountant_agent,
    "ConciergeAgent": concierge_agent,
    "GroupAgent": group_agent,
}

# Which specialist agent handles which intent. Unmapped intents use BaseAgent.
_INTENT_AGENT: dict[str, str] = {
    Intent.RECEIPT_OCR: "AccountantAgent",
    Intent.BOOKING: "ConciergeAgent",
}


def _matches(text: str, words: set[str], bank: set[str]) -> bool:
    """True if any keyword in ``bank`` is present.

    Single-word keywords match on whole-word boundaries (via the ``words`` token
    set, so "hi" won't match inside "this"). Multi-word keywords (e.g.
    "good morning", "how much", "ቀጠሮ ያዝ") match as substrings of the raw text —
    something the old single-token set intersection could never do.
    """
    for kw in bank:
        if " " in kw:
            if kw in text:
                return True
        elif kw in words:
            return True
    return False


class IntentRouter:
    """
    Lightweight keyword-based intent classifier.
    Falls back to GENERAL_QA for anything unmatched.
    In a later phase this will be replaced by an LLM classifier,
    but keywords cover >80% of common SMB customer messages with zero latency.
    """

    def classify(self, envelope: MessageEnvelope) -> str:
        text = (envelope.text or "").lower().strip()

        # Media with no text → likely a receipt image for the Accountant agent
        if envelope.media_type in ("photo", "document") and not text:
            return Intent.RECEIPT_OCR

        if not text:
            return Intent.GENERAL_QA

        # Token set for whole-word matching; _matches also does substring
        # matching for multi-word phrases against the raw text.
        words = set(re.findall(r"[\wሀ-፿]+", text))

        if _matches(text, words, _HANDOFF_WORDS):
            return Intent.HUMAN_HANDOFF

        if _matches(text, words, _COMPLAINT_WORDS):
            return Intent.COMPLAINT

        if _matches(text, words, _BOOKING_WORDS):
            return Intent.BOOKING

        if _matches(text, words, _ORDER_WORDS):
            return Intent.ORDER

        if _matches(text, words, _PRICE_WORDS):
            return Intent.PRICE_CHECK

        if _matches(text, words, _GREETING_WORDS) and len(words) <= 5:
            return Intent.GREETING

        return Intent.GENERAL_QA

    def select_agent(
        self,
        intent: str,
        available_agents: Optional[list[str]] = None,
    ) -> BaseAgent:
        """
        Map an intent to the agent instance that should handle it.

        ``available_agents`` optionally restricts routing to the ChildAgent
        types a business has deployed: when provided, a specialist is only
        chosen if it's in that list; when None, specialists are available by
        default. Always falls back to the general-purpose BaseAgent.
        """
        target = _INTENT_AGENT.get(intent)
        if target and (available_agents is None or target in set(available_agents)):
            agent = _AGENT_REGISTRY.get(target)
            if agent is not None:
                return agent
        return _AGENT_REGISTRY["BaseAgent"]


intent_router = IntentRouter()
