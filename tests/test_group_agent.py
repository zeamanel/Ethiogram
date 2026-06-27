"""Tests for the Community Assistant (group) agent: chat-type parsing, the
mention/reply addressing gate, routing, and the seeded father + free model."""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agents.group import group_agent
from app.api.webhooks import _agent_type_for, _is_addressed
from app.services.telegram_service import telegram_service


# ── parsing: group vs private identity ────────────────────────────────────────

def _update(chat_type, chat_id, sender_id, text="hi", reply_from=None):
    msg = {"message_id": 1, "date": 0, "text": text,
           "from": {"id": sender_id, "first_name": "Abe", "username": "abe"},
           "chat": {"id": chat_id, "type": chat_type}}
    if reply_from is not None:
        msg["reply_to_message"] = {"from": reply_from}
    return {"message": msg}


def test_group_message_identity_is_the_group():
    env = telegram_service.parse_incoming_update(_update("supergroup", -100123, 555), "h")
    assert env.chat_type == "supergroup"
    assert env.customer_id == "-100123"     # the GROUP, not the sender — replies go to the group


def test_private_message_identity_is_the_user():
    env = telegram_service.parse_incoming_update(_update("private", 555, 555), "h")
    assert env.chat_type == "private"
    assert env.customer_id == "555"


# ── addressing gate ───────────────────────────────────────────────────────────

def _env(raw):
    return SimpleNamespace(raw=raw)


def test_addressed_by_mention():
    bot = SimpleNamespace(bot_username="selam_bot")
    env = _env(_update("supergroup", -1, 5, text="hey @Selam_Bot what are your hours?"))
    assert _is_addressed(env, bot) is True


def test_addressed_by_reply_to_bot():
    bot = SimpleNamespace(bot_username="selam_bot")
    raw = _update("supergroup", -1, 5, text="and delivery?",
                  reply_from={"is_bot": True, "username": "selam_bot"})
    assert _is_addressed(_env(raw), bot) is True


def test_not_addressed_plain_message():
    bot = SimpleNamespace(bot_username="selam_bot")
    env = _env(_update("supergroup", -1, 5, text="anyone going to the meetup?"))
    assert _is_addressed(env, bot) is False


def test_not_addressed_reply_to_other_bot():
    bot = SimpleNamespace(bot_username="selam_bot")
    raw = _update("supergroup", -1, 5, text="thanks",
                  reply_from={"is_bot": True, "username": "some_other_bot"})
    assert _is_addressed(_env(raw), bot) is False


# ── routing + agent ───────────────────────────────────────────────────────────

def test_community_category_routes_to_group_agent():
    father = SimpleNamespace(category="Group & Community", tags=["group"], capabilities=[])
    assert _agent_type_for(father) == "GroupAgent"


def test_group_agent_prompt_has_group_tone():
    prompt = group_agent.build_system_prompt(brain_config=None, child_data=None, chunks=[])
    assert "group chat" in prompt.lower()
    assert group_agent.agent_name == "Community Assistant"


# ── seed: father + free model ─────────────────────────────────────────────────

def test_seed_includes_community_assistant_on_free_model():
    from workers.seed_agents import SEED_AGENTS, SEED_MODELS
    ca = next(s for s in SEED_AGENTS if s["name"] == "Community Assistant")
    assert ca["preferred_model_id"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert _agent_type_for(SimpleNamespace(
        category=ca["category"], tags=ca["tags"], capabilities=ca["capabilities"])) == "GroupAgent"
    assert any(m["model_id"] == "meta-llama/llama-3.3-70b-instruct:free" for m in SEED_MODELS)


@pytest.mark.asyncio
async def test_seeded_community_assistant_persists_free_model(db):
    from app.db.models import Agent
    from workers.seed_agents import SEED_AGENTS, _ensure_publisher, _upsert_agent, _upsert_model, SEED_MODELS
    for m in SEED_MODELS:                       # models first (FK target)
        await _upsert_model(db, m)
    profile = await _ensure_publisher(db)
    ca = next(s for s in SEED_AGENTS if s["name"] == "Community Assistant")
    await _upsert_agent(db, profile, ca)
    row = (await db.execute(select(Agent).where(Agent.name == "Community Assistant"))).scalar_one()
    assert row.preferred_model_id == "meta-llama/llama-3.3-70b-instruct:free"
