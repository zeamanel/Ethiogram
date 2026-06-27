"""Tests for wiring per-business ChildAgent config (child_data) into the agents.

Covers:
  - _agent_type_for: maps a marketplace father Agent to the local agent class
  - _load_child_data: returns a deployed business's config + decrypted father
    prompt for a given agent type (and None when nothing is deployed)
  - the loaded child_data actually surfaces in a specialist's system prompt
"""
import uuid

import pytest

from app.agents.base import base_agent
from app.agents.concierge import concierge_agent
from app.api.webhooks import _agent_type_for, _load_child_data
from app.core.security import encrypt_agent_prompt, encrypt_child_secrets
from app.db.models import Agent, AgentStatus, ChildAgent


class _FakeFather:
    """Duck-typed stand-in for a father Agent (only the fields _agent_type_for reads)."""
    def __init__(self, category="", tags=None, capabilities=None):
        self.category = category
        self.tags = tags or []
        self.capabilities = capabilities or []


def test_agent_type_for_mapping():
    assert _agent_type_for(_FakeFather(category="Accounting")) == "AccountantAgent"
    assert _agent_type_for(_FakeFather(tags=["receipt", "ocr"])) == "AccountantAgent"
    assert _agent_type_for(_FakeFather(category="Concierge & Booking")) == "ConciergeAgent"
    assert _agent_type_for(_FakeFather(capabilities=["appointment scheduling"])) == "ConciergeAgent"
    assert _agent_type_for(_FakeFather(category="general qa")) == "BaseAgent"


async def _deploy_agent(db, business_id, *, category, child_data, prompt, secrets=None,
                        preferred_model_id=None):
    enc, key_ref = encrypt_agent_prompt(prompt, str(uuid.uuid4()))
    father = Agent(
        creator_id=uuid.uuid4(), name="A", tagline="t", description="d",
        category=category, tags=[], capabilities=[],
        encrypted_system_prompt=enc, encryption_key_ref=key_ref,
        price_etg=100, status=AgentStatus.live, preferred_model_id=preferred_model_id,
    )
    db.add(father)
    await db.flush()
    child = ChildAgent(
        agent_id=father.id, business_id=business_id, is_active=True,
        child_data=child_data,
        child_secrets=encrypt_child_secrets(secrets) if secrets else None,
    )
    db.add(child)
    await db.flush()
    return father, child


@pytest.mark.asyncio
async def test_load_child_data_returns_config_and_father_prompt(db):
    biz = uuid.uuid4()
    await _deploy_agent(
        db, biz, category="concierge",
        child_data={"services": "Haircut", "business_hours": "9-5", "timezone": "Africa/Addis_Ababa"},
        prompt="You are a booking pro.",
    )

    data = await _load_child_data(str(biz), "ConciergeAgent", db)
    assert data is not None
    assert data["services"] == "Haircut"
    assert data["business_hours"] == "9-5"
    assert data["_father_prompt"] == "You are a booking pro."

    # A type the business hasn't deployed → None (agent uses generic behaviour).
    assert await _load_child_data(str(biz), "AccountantAgent", db) is None


@pytest.mark.asyncio
async def test_load_child_data_surfaces_father_model(db):
    """The admin-set father model flows to the runtime via _father_model_id."""
    biz = uuid.uuid4()
    await _deploy_agent(db, biz, category="concierge", child_data={"services": "X"},
                        prompt="p", preferred_model_id="anthropic/claude-3.5-haiku")
    data = await _load_child_data(str(biz), "ConciergeAgent", db)
    assert data["_father_model_id"] == "anthropic/claude-3.5-haiku"


@pytest.mark.asyncio
async def test_load_child_data_none_when_nothing_deployed(db):
    assert await _load_child_data(str(uuid.uuid4()), "ConciergeAgent", db) is None


@pytest.mark.asyncio
async def test_inactive_child_agent_is_ignored(db):
    biz = uuid.uuid4()
    father, child = await _deploy_agent(
        db, biz, category="concierge", child_data={"services": "X"}, prompt="p",
    )
    child.is_active = False
    await db.flush()
    assert await _load_child_data(str(biz), "ConciergeAgent", db) is None


def test_child_data_surfaces_in_concierge_prompt():
    prompt = concierge_agent.build_system_prompt(
        brain_config=None,
        child_data={
            "services": "Haircut, Spa",
            "business_hours": "10-6",
            "timezone": "Africa/Addis_Ababa",
            "_father_prompt": "Be a great concierge.",
        },
        chunks=[],
    )
    assert "Haircut, Spa" in prompt      # owner's real services
    assert "10-6" in prompt              # owner's real hours
    assert "Africa/Addis_Ababa" in prompt
    assert "Be a great concierge." in prompt   # father agent prompt


@pytest.mark.asyncio
async def test_child_secrets_decrypted_into_underscore_secrets(db):
    biz = uuid.uuid4()
    await _deploy_agent(
        db, biz, category="concierge",
        child_data={"services": "Haircut"},
        prompt="p",
        secrets={"calendar_id": "cal@x.com", "credentials_json": '{"token":"SENSITIVE"}'},
    )
    data = await _load_child_data(str(biz), "ConciergeAgent", db)
    assert data is not None
    # secrets are decrypted and exposed under the reserved _secrets key
    assert data["_secrets"]["calendar_id"] == "cal@x.com"
    assert data["_secrets"]["credentials_json"] == '{"token":"SENSITIVE"}'
    # plain config is still there
    assert data["services"] == "Haircut"


def test_secrets_never_render_into_prompt():
    prompt = base_agent.build_system_prompt(
        brain_config=None,
        child_data={
            "services": "Haircut",
            "_father_prompt": "Be helpful.",
            "_secrets": {"credentials_json": '{"token":"SENSITIVE"}', "api_key": "sk-LEAK"},
        },
        chunks=[],
    )
    assert "Haircut" in prompt              # plain config rendered
    assert "Be helpful." in prompt          # father prompt rendered
    assert "SENSITIVE" not in prompt        # secret value must NOT leak
    assert "sk-LEAK" not in prompt
    assert "_secrets" not in prompt
    assert "credentials_json" not in prompt


def test_rendering_is_prose_not_python_repr():
    prompt = base_agent.build_system_prompt(
        brain_config=None,
        child_data={
            "menu_items": ["Burger", "Fries", "Cola"],
            "hours": {"mon": "9-5", "sun": "closed"},
            "delivery": True,
        },
        chunks=[],
    )
    # lists/dicts/bools become readable text, not ['..'] / {'..'} / True
    assert "Burger, Fries, Cola" in prompt
    assert "['Burger'" not in prompt and "[" not in prompt.split("Business-specific")[1]
    assert "mon: 9-5" in prompt
    assert "Delivery: yes" in prompt
