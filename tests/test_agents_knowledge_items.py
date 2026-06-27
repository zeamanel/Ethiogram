"""Phase C: the structured catalog (knowledge_items) must reach the bot.

Unlike documents (retrieved by similarity), knowledge_items are curated rows a
business always wants the bot to see — so process() appends them verbatim to the
system prompt. These tests prove:
  - the formatter renders title / body / price as readable text
  - inactive items and missing fields are handled
  - process() actually injects the catalog block into the system prompt
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.agents.base import base_agent
from app.db.models import (
    BusinessBrainConfig,
    Conversation,
    KnowledgeItem,
    KnowledgeItemType,
)
from app.services import rag_service as rag_mod
from app.services.rag_service import rag_service
from app.services.telegram_service import MessageEnvelope


def _item(item_type, title, body=None, data=None, is_active=True):
    return KnowledgeItem(
        id=uuid.uuid4(), business_id=uuid.uuid4(), item_type=item_type,
        title=title, body=body, data=data, is_active=is_active,
    )


# ── formatter unit tests ─────────────────────────────────────────────────────

def test_formatter_renders_title_body_and_price():
    items = [
        _item(KnowledgeItemType.product, "Blue Dress",
              body="Flowing summer dress in cotton.", data={"price": "1200 ETB"}),
        _item(KnowledgeItemType.faq, "Do you deliver?",
              body="Yes, free delivery over 1000 ETB."),
    ]
    out = rag_service.format_knowledge_items_for_prompt(items)
    assert "=== Business catalog ===" in out
    assert "Blue Dress" in out
    assert "Flowing summer dress" in out
    assert "1200 ETB" in out
    assert "[PRODUCT]" in out and "[FAQ]" in out
    assert "Do you deliver?" in out


def test_formatter_empty_when_no_items():
    assert rag_service.format_knowledge_items_for_prompt([]) == ""


# ── process() injection (the wiring that was previously dead) ────────────────

def _envelope(text):
    return MessageEnvelope(
        platform="telegram", business_id=None, bot_id=None, token_hash="x",
        customer_id="123", customer_name="Abebe", customer_username=None,
        text=text, media_type=None, media_url=None, media_file_id=None,
        message_id=1, timestamp=datetime.now(timezone.utc), raw={},
    )


@pytest.mark.asyncio
async def test_process_injects_catalog_into_system_prompt(db, monkeypatch):
    biz_id = uuid.uuid4()
    conv = Conversation(
        id=uuid.uuid4(), business_id=biz_id, bot_id=uuid.uuid4(),
        customer_platform_id="123",
    )
    brain = BusinessBrainConfig(business_id=biz_id, persona_name="Selam")

    # Avoid pgvector SQL on SQLite + the UUID/str comparison in the real query;
    # this test is about the *wiring*, so stub the two rag_service reads.
    captured = {}

    async def _no_search(**kwargs):
        return []

    async def _items(business_id, db, active_only=True):
        assert business_id == str(biz_id)      # process passes the str id
        assert active_only is True             # only live catalog rows
        return [_item(KnowledgeItemType.product, "ZEBRA-Dress",
                      body="rare", data={"price": "999 ETB"})]

    async def _fake_model(**kwargs):
        captured["system_prompt"] = kwargs["system_prompt"]
        return "ok", {"input_tokens": 1, "output_tokens": 1}, "test-model"

    monkeypatch.setattr(rag_mod.rag_service, "search", _no_search)
    monkeypatch.setattr(rag_mod.rag_service, "get_knowledge_items", _items)
    monkeypatch.setattr(
        "app.services.model_router.model_router.execute_with_fallback", _fake_model
    )

    await base_agent.process(
        envelope=_envelope("show me dresses"),
        conversation=conv,
        brain_config=brain,
        child_data=None,
        db=db,
    )

    sp = captured["system_prompt"]
    assert "=== Business catalog ===" in sp     # block injected
    assert "ZEBRA-Dress" in sp                  # the curated item is present
    assert "999 ETB" in sp


@pytest.mark.asyncio
async def test_process_forwards_agent_model_id(db, monkeypatch):
    """The admin-set father model reaches the model router via process()."""
    conv = Conversation(id=uuid.uuid4(), business_id=uuid.uuid4(), bot_id=uuid.uuid4(),
                        customer_platform_id="1")
    captured = {}

    async def _no_search(**k):
        return []

    async def _items(*a, **k):
        return []

    async def _fake_model(**kwargs):
        captured["agent_model_id"] = kwargs.get("agent_model_id")
        return "ok", {"input_tokens": 1, "output_tokens": 1}, "test-model"

    monkeypatch.setattr(rag_mod.rag_service, "search", _no_search)
    monkeypatch.setattr(rag_mod.rag_service, "get_knowledge_items", _items)
    monkeypatch.setattr("app.services.model_router.model_router.execute_with_fallback", _fake_model)

    await base_agent.process(
        envelope=_envelope("hi"), conversation=conv, brain_config=None,
        child_data=None, db=db, agent_model_id="anthropic/claude-3.5-haiku",
    )
    assert captured["agent_model_id"] == "anthropic/claude-3.5-haiku"


@pytest.mark.asyncio
async def test_process_without_brain_config_skips_catalog(db, monkeypatch):
    conv = Conversation(
        id=uuid.uuid4(), business_id=uuid.uuid4(), bot_id=uuid.uuid4(),
        customer_platform_id="123",
    )
    called = {"items": False}

    async def _items(*a, **k):
        called["items"] = True
        return []

    async def _fake_model(**kwargs):
        return "ok", {"input_tokens": 1, "output_tokens": 1}, "test-model"

    monkeypatch.setattr(rag_mod.rag_service, "get_knowledge_items", _items)
    monkeypatch.setattr(
        "app.services.model_router.model_router.execute_with_fallback", _fake_model
    )

    # No brain_config -> no RAG search, no catalog load.
    await base_agent.process(
        envelope=_envelope("hi"),
        conversation=conv,
        brain_config=None,
        child_data=None,
        db=db,
    )
    assert called["items"] is False
