"""STEP 3/4: prove the webhook routes each message to the correct agent.

_process_message must classify intent and dispatch to the right agent
singleton: receipts -> Accountant, bookings -> Concierge, general -> Base Q&A.
Each agent's process() is spied on, so no real model/RAG/OCR call is made.
"""
from datetime import datetime, timezone

import pytest

import app.agents.accountant as acc_mod
import app.agents.base as base_mod
import app.agents.concierge as con_mod
from app.agents.base import AgentResponse
from app.api.webhooks import _process_message
from app.services.telegram_service import MessageEnvelope


def _env(text=None, media_type=None):
    return MessageEnvelope(
        platform="telegram", business_id="biz", bot_id="bot", token_hash="t",
        customer_id="c", customer_name="C", customer_username=None,
        text=text, media_type=media_type, media_url=None,
        media_file_id=("f" if media_type else None),
        message_id=1, timestamp=datetime.now(timezone.utc), raw={},
    )


@pytest.fixture
def spy_agents(monkeypatch):
    """Replace each agent singleton's process() with a recorder."""
    calls: list[str] = []

    def _make(tag):
        async def _proc(**kwargs):
            calls.append(tag)
            return AgentResponse(
                text=f"reply from {tag}", model_id="test-model",
                input_tokens=3, output_tokens=4, chunks_retrieved=[],
            )
        return _proc

    monkeypatch.setattr(base_mod.base_agent, "process", _make("base"))
    monkeypatch.setattr(acc_mod.accountant_agent, "process", _make("accountant"))
    monkeypatch.setattr(con_mod.concierge_agent, "process", _make("concierge"))
    return calls


@pytest.mark.asyncio
async def test_receipt_message_routes_to_accountant(spy_agents):
    text, tokens, model_id = await _process_message(
        _env(media_type="photo"), conversation=None, brain_config=None, db=None,
    )
    assert spy_agents == ["accountant"]
    assert text == "reply from accountant"
    assert tokens == {"input_tokens": 3, "output_tokens": 4}
    assert model_id == "test-model"


@pytest.mark.asyncio
async def test_booking_message_routes_to_concierge(spy_agents):
    text, _, _ = await _process_message(
        _env(text="can i book an appointment"), conversation=None,
        brain_config=None, db=None,
    )
    assert spy_agents == ["concierge"]
    assert text == "reply from concierge"


@pytest.mark.asyncio
async def test_general_question_routes_to_base(spy_agents):
    text, _, _ = await _process_message(
        _env(text="who is nikola tesla"), conversation=None,
        brain_config=None, db=None,
    )
    assert spy_agents == ["base"]
    assert text == "reply from base"


@pytest.mark.asyncio
async def test_agent_failure_falls_back_to_model_router(monkeypatch):
    """If the agent raises, the bot must not go silent — fall back to a
    direct model_router call."""
    async def _boom(**kwargs):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(base_mod.base_agent, "process", _boom)

    async def _fake_model(**kwargs):
        return "fallback reply", {"input_tokens": 1, "output_tokens": 1}, "fb-model"

    monkeypatch.setattr(
        "app.services.model_router.model_router.execute_with_fallback", _fake_model
    )

    text, _, model_id = await _process_message(
        _env(text="who is nikola tesla"), conversation=None,
        brain_config=None, db=None,
    )
    assert text == "fallback reply"
    assert model_id == "fb-model"


@pytest.mark.asyncio
async def test_empty_message_returns_fallback_without_agent(spy_agents):
    text, tokens, model_id = await _process_message(
        _env(text="   "), conversation=None, brain_config=None, db=None,
    )
    assert spy_agents == []          # no agent invoked
    assert model_id == "none"
    assert tokens == {"input_tokens": 0, "output_tokens": 0}
