"""Tests for wiring per-business ChildAgent config (child_data) into the agents.

Covers:
  - _agent_type_for: maps a marketplace father Agent to the local agent class
  - _load_child_data: returns a deployed business's config + decrypted father
    prompt for a given agent type (and None when nothing is deployed)
  - the loaded child_data actually surfaces in a specialist's system prompt
"""
import uuid

import pytest

from app.agents.concierge import concierge_agent
from app.api.webhooks import _agent_type_for, _load_child_data
from app.core.security import encrypt_agent_prompt
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


async def _deploy_agent(db, business_id, *, category, child_data, prompt):
    enc, key_ref = encrypt_agent_prompt(prompt, str(uuid.uuid4()))
    father = Agent(
        creator_id=uuid.uuid4(), name="A", tagline="t", description="d",
        category=category, tags=[], capabilities=[],
        encrypted_system_prompt=enc, encryption_key_ref=key_ref,
        price_etg=100, status=AgentStatus.live,
    )
    db.add(father)
    await db.flush()
    child = ChildAgent(
        agent_id=father.id, business_id=business_id, is_active=True, child_data=child_data,
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
