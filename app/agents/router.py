# app/agents/router.py
from __future__ import annotations

import re
from typing import Optional

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

        # Exact / partial keyword matching
        words = set(re.findall(r"[\w\u1200-\u137f]+", text))

        if words & _HANDOFF_WORDS:
            return Intent.HUMAN_HANDOFF

        if words & _COMPLAINT_WORDS:
            return Intent.COMPLAINT

        if words & _BOOKING_WORDS:
            return Intent.BOOKING

        if words & _ORDER_WORDS:
            return Intent.ORDER

        if words & _PRICE_WORDS:
            return Intent.PRICE_CHECK

        if words & _GREETING_WORDS and len(words) <= 5:
            return Intent.GREETING

        return Intent.GENERAL_QA

    def select_agent(
        self,
        intent: str,
        available_agents: Optional[list[str]] = None,
    ) -> str:
        """
        Map intent → agent class name.
        `available_agents` is the list of ChildAgent types deployed for this business.
        Falls back to 'BaseAgent' when a specialist isn't available.
        """
        deployed = set(available_agents or [])

        if intent == Intent.RECEIPT_OCR and "AccountantAgent" in deployed:
            return "AccountantAgent"

        if intent == Intent.BOOKING and "ConciergeAgent" in deployed:
            return "ConciergeAgent"

        if intent in (Intent.ORDER, Intent.PRICE_CHECK, Intent.PRODUCT_INQUIRY):
            if "SalesCloserAgent" in deployed:
                return "SalesCloserAgent"

        return "BaseAgent"


intent_router = IntentRouter()
