"""Tests for the extended /dashboard/overview (active_agents + brain_summary)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import encrypt_agent_prompt
from app.db.models import (
    Agent, AgentStatus, AgentTrial, AgentUnlock, Business, BusinessBrainConfig,
    ChildAgent, DocumentStatus, KnowledgeChunk, KnowledgeDocument, KnowledgeItem,
    KnowledgeItemType, User,
)


async def _seed(db, user_id, business_id):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Selam Store", slug=f"b{business_id.hex[:6]}"))
    db.add(BusinessBrainConfig(business_id=business_id, persona_name="Selam"))
    # docs across statuses
    for st in (DocumentStatus.completed, DocumentStatus.completed,
               DocumentStatus.processing, DocumentStatus.failed):
        db.add(KnowledgeDocument(business_id=business_id, filename="d.txt", file_type="txt",
                                 file_size_bytes=10, gcs_path="p", status=st, uploaded_by_id=user_id))
    # chunks + items
    for i in range(3):
        db.add(KnowledgeChunk(business_id=business_id, content="c", token_count=5, chunk_index=i))
    db.add(KnowledgeItem(business_id=business_id, item_type=KnowledgeItemType.product, title="Injera"))
    await db.flush()


async def _deploy(db, business_id, category="concierge"):
    enc, ref = encrypt_agent_prompt("p", str(uuid.uuid4()))
    father = Agent(creator_id=uuid.uuid4(), name="A", tagline="t", description="d",
                   category=category, tags=[], capabilities=[], encrypted_system_prompt=enc,
                   encryption_key_ref=ref, price_etg=100, status=AgentStatus.live)
    db.add(father); await db.flush()
    child = ChildAgent(agent_id=father.id, business_id=business_id, display_name="Booker", is_active=True)
    db.add(child); await db.flush()
    return father, child


@pytest.mark.asyncio
async def test_overview_brain_summary(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.get(f"/api/v1/dashboard/overview/{sample_business_id}",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    brain = resp.json()["brain_summary"]
    assert brain["persona_name"] == "Selam"
    assert brain["total_chunks"] == 3
    assert brain["total_items"] == 1
    assert brain["docs_by_status"] == {"embedded": 2, "processing": 1, "failed": 1}


@pytest.mark.asyncio
async def test_overview_active_agents_trial(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    _, child = await _deploy(db, sample_business_id)
    db.add(AgentTrial(agent_id=child.agent_id, business_id=sample_business_id, child_agent_id=child.id,
                      expires_at=datetime.now(timezone.utc) + timedelta(days=10)))
    await db.flush()

    resp = await client.get(f"/api/v1/dashboard/overview/{sample_business_id}",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    agents = resp.json()["active_agents"]
    assert len(agents) == 1
    a = agents[0]
    assert a["display_name"] == "Booker"
    assert a["agent_id"] == str(child.agent_id)   # father id, for "deployed" marking
    assert a["category"] == "concierge"
    assert a["status"] == "trial"
    assert a["days_left"] in (9, 10)   # ~10 days, .days truncation


@pytest.mark.asyncio
async def test_overview_active_agents_unlocked(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    _, child = await _deploy(db, sample_business_id)
    db.add(AgentUnlock(agent_id=child.agent_id, business_id=sample_business_id,
                       child_agent_id=child.id, etg_paid=100))
    await db.flush()

    resp = await client.get(f"/api/v1/dashboard/overview/{sample_business_id}",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    a = resp.json()["active_agents"][0]
    assert a["status"] == "unlocked"
    assert a["days_left"] is None


@pytest.mark.asyncio
async def test_overview_empty_when_no_brain_or_agents(client, db, sample_user_id, sample_business_id, valid_access_token):
    db.add(User(id=sample_user_id))
    db.add(Business(id=sample_business_id, owner_id=sample_user_id, name="Bare", slug="bare"))
    await db.flush()
    resp = await client.get(f"/api/v1/dashboard/overview/{sample_business_id}",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_agents"] == []
    assert body["brain_summary"]["persona_name"] is None
    assert body["brain_summary"]["docs_by_status"] == {"embedded": 0, "processing": 0, "failed": 0}
