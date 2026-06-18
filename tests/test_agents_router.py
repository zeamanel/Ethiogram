"""Tests for app.agents.router (intent classification + agent selection).

Covers the STEP 1 bug fixes:
  - multi-word phrases now match ("good morning", "how much", ...)
  - single-word keywords still match on word boundaries (no "hi" in "this")
  - select_agent returns a usable agent instance, not a class-name string
  - the non-existent SalesCloserAgent reference is gone
"""
from datetime import datetime, timezone

import pytest

from app.agents.accountant import AccountantAgent
from app.agents.base import BaseAgent
from app.agents.concierge import ConciergeAgent
from app.agents.router import Intent, intent_router
from app.services.telegram_service import MessageEnvelope


def _env(text=None, media_type=None):
    return MessageEnvelope(
        platform="telegram", business_id="b", bot_id="bot", token_hash="t",
        customer_id="c", customer_name="C", customer_username=None,
        text=text, media_type=media_type, media_url=None,
        media_file_id=("f" if media_type else None),
        message_id=1, timestamp=datetime.now(timezone.utc), raw={},
    )


@pytest.mark.parametrize("text,expected", [
    ("good morning!", Intent.GREETING),           # multi-word (was broken)
    ("how much is this", Intent.PRICE_CHECK),      # multi-word (was broken)
    ("i want to add to cart", Intent.ORDER),       # multi-word
    ("can i book an appointment", Intent.BOOKING),
    ("the product is broken", Intent.COMPLAINT),
    ("let me talk to a human", Intent.HUMAN_HANDOFF),
    ("who is nikola tesla", Intent.GENERAL_QA),
])
def test_classify_intents(text, expected):
    assert intent_router.classify(_env(text=text)) == expected


def test_single_word_keyword_not_matched_inside_other_words():
    # "hi" must not match inside "this"/"thistle".
    assert intent_router.classify(_env(text="this thistle")) == Intent.GENERAL_QA


def test_media_without_text_routes_to_receipt():
    assert intent_router.classify(_env(media_type="photo")) == Intent.RECEIPT_OCR


def test_select_agent_returns_instances():
    assert isinstance(intent_router.select_agent(Intent.RECEIPT_OCR), AccountantAgent)
    assert isinstance(intent_router.select_agent(Intent.BOOKING), ConciergeAgent)
    general = intent_router.select_agent(Intent.GENERAL_QA)
    assert type(general) is BaseAgent


def test_deployment_filter_falls_back_to_base():
    # When the business hasn't deployed the specialist, fall back to BaseAgent.
    agent = intent_router.select_agent(Intent.RECEIPT_OCR, available_agents=[])
    assert type(agent) is BaseAgent
    # But an explicit allow-list including it routes to the specialist.
    agent = intent_router.select_agent(Intent.RECEIPT_OCR, available_agents=["AccountantAgent"])
    assert isinstance(agent, AccountantAgent)


def test_no_salescloser_reference():
    import app.agents.router as r
    assert "SalesCloserAgent" not in open(r.__file__, encoding="utf-8").read()
