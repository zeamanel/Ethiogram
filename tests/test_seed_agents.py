"""Tests for the first-party agent seeder (workers/seed_agents.py).

Proves the seed is idempotent, that each seeded agent routes to the specialist
class it was written for, that the encrypted prompt round-trips, and that the
Concierge child_schema matches the keys ConciergeAgent actually reads.
"""
import pytest
from sqlalchemy import select

from app.api.webhooks import _agent_type_for
from app.core.security import decrypt_agent_prompt
from app.db.models import Agent, AgentStatus, CreatorProfile, User
from workers.seed_agents import (
    SEED_AGENTS,
    _ensure_publisher,
    _upsert_agent,
)


@pytest.mark.asyncio
async def test_ensure_publisher_is_idempotent(db):
    p1 = await _ensure_publisher(db)
    p2 = await _ensure_publisher(db)
    assert p1.id == p2.id
    assert p1.is_trusted is True                       # so agents go live
    users = (await db.execute(select(User))).scalars().all()
    profiles = (await db.execute(select(CreatorProfile))).scalars().all()
    assert len(users) == 1 and len(profiles) == 1      # no duplicates


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(db):
    profile = await _ensure_publisher(db)
    spec = SEED_AGENTS[0]

    assert await _upsert_agent(db, profile, spec) == "created"
    assert await _upsert_agent(db, profile, spec) == "updated"   # 2nd run = update

    agents = (await db.execute(select(Agent).where(Agent.name == spec["name"]))).scalars().all()
    assert len(agents) == 1                                       # no duplicate row
    assert agents[0].status == AgentStatus.live


@pytest.mark.asyncio
async def test_seeded_agents_route_to_their_specialist(db):
    profile = await _ensure_publisher(db)
    for spec in SEED_AGENTS:
        await _upsert_agent(db, profile, spec)
    agents = {a.name: a for a in (await db.execute(select(Agent))).scalars().all()}

    assert _agent_type_for(agents["Booking Concierge"]) == "ConciergeAgent"
    assert _agent_type_for(agents["Receipt & Expense Assistant"]) == "AccountantAgent"


@pytest.mark.asyncio
async def test_seeded_prompt_round_trips(db):
    profile = await _ensure_publisher(db)
    await _upsert_agent(db, profile, SEED_AGENTS[0])
    agent = (await db.execute(
        select(Agent).where(Agent.name == "Booking Concierge")
    )).scalar_one()
    decrypted = decrypt_agent_prompt(agent.encrypted_system_prompt, agent.encryption_key_ref)
    assert "booking concierge" in decrypted.lower()
    # ciphertext must not be the plaintext
    assert agent.encrypted_system_prompt != SEED_AGENTS[0]["system_prompt"]


def test_concierge_schema_matches_agent_keys():
    # The Concierge child_schema must expose exactly the keys ConciergeAgent reads,
    # or owners can't configure it and the specialist runs on defaults.
    concierge = next(s for s in SEED_AGENTS if s["name"] == "Booking Concierge")
    keys = {f["key"] for f in concierge["child_schema"]["fields"]}
    assert {"services", "appointment_duration_minutes", "business_hours", "timezone"} <= keys


def test_concierge_secret_fields_match_calendar_creds():
    # The secret_fields keys MUST match what ConciergeAgent._calendar_creds reads
    # from _secrets, or a connected calendar still won't work.
    concierge = next(s for s in SEED_AGENTS if s["name"] == "Booking Concierge")
    secret_keys = {f["key"] for f in concierge["child_schema"]["secret_fields"]}
    assert secret_keys == {"calendar_id", "credentials_json"}
