"""End-to-end 'Browse → Deploy' flow, exercising the exact endpoints the Mini
App calls: GET /agents (marketplace) → POST /agents/{id}/trial → the agent then
shows under /dashboard/overview active_agents (where Phase D manages it).

Uses the real first-party seeder so the seed + browse + deploy path is proven
together.
"""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Agent, Business, ChildAgent, User
from workers.seed_agents import _ensure_publisher, _upsert_agent, SEED_AGENTS


async def _seed_owner_business(db, user_id, business_id):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Biz", slug=f"b-{business_id.hex[:8]}"))
    await db.flush()


async def _seed_marketplace(db):
    profile = await _ensure_publisher(db)
    for spec in SEED_AGENTS:
        await _upsert_agent(db, profile, spec)
    await db.flush()


@pytest.mark.asyncio
async def test_browse_lists_seeded_agents(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed_owner_business(db, sample_user_id, sample_business_id)
    await _seed_marketplace(db)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    resp = await client.get("/api/v1/agents", headers=hdr)
    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()}
    assert "Booking Concierge" in names           # live agents are browsable
    # the concierge exposes its config schema for the deploy form
    concierge = next(a for a in resp.json() if a["name"] == "Booking Concierge")
    assert concierge["child_schema"]["fields"]


@pytest.mark.asyncio
async def test_deploy_starts_trial_and_shows_in_overview(client, db, sample_user_id,
                                                         sample_business_id, valid_access_token):
    await _seed_owner_business(db, sample_user_id, sample_business_id)
    await _seed_marketplace(db)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    agent = (await db.execute(
        select(Agent).where(Agent.name == "Booking Concierge")
    )).scalar_one()

    # deploy = start trial with config collected from the schema
    deploy = await client.post(
        f"/api/v1/agents/{agent.id}/trial",
        json={"business_id": str(sample_business_id),
              "child_data": {"services": "Haircut", "timezone": "Africa/Addis_Ababa"}},
        headers=hdr,
    )
    assert deploy.status_code == 201, deploy.text
    child_id = deploy.json()["child_agent_id"]

    # the child agent exists with the config we sent
    child = (await db.execute(
        select(ChildAgent).where(ChildAgent.id == uuid.UUID(child_id))
    )).scalar_one()
    assert child.child_data["services"] == "Haircut"

    # and it now surfaces under the dashboard's active_agents (Phase D entry point)
    ov = await client.get(f"/api/v1/dashboard/overview/{sample_business_id}", headers=hdr)
    agents = ov.json()["active_agents"]
    assert len(agents) == 1
    assert agents[0]["agent_id"] == str(agent.id)   # marks it "Deployed" in browse
    assert agents[0]["status"] == "trial"


@pytest.mark.asyncio
async def test_deploy_twice_is_rejected(client, db, sample_user_id,
                                        sample_business_id, valid_access_token):
    await _seed_owner_business(db, sample_user_id, sample_business_id)
    await _seed_marketplace(db)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    agent = (await db.execute(
        select(Agent).where(Agent.name == "Booking Concierge")
    )).scalar_one()
    body = {"business_id": str(sample_business_id), "child_data": {}}

    first = await client.post(f"/api/v1/agents/{agent.id}/trial", json=body, headers=hdr)
    assert first.status_code == 201
    again = await client.post(f"/api/v1/agents/{agent.id}/trial", json=body, headers=hdr)
    assert again.status_code == 409   # one trial per agent per business


@pytest.mark.asyncio
async def test_trial_reuses_existing_child_agent(client, db, sample_user_id,
                                                 sample_business_id, valid_access_token):
    """A ChildAgent already deployed (no trial yet) must be reused, not duplicated
    — a duplicate insert would violate uq_child_agent_business and 500."""
    from app.db.models import ChildAgent
    await _seed_owner_business(db, sample_user_id, sample_business_id)
    await _seed_marketplace(db)
    agent = (await db.execute(
        select(Agent).where(Agent.name == "Receipt & Expense Assistant"))).scalar_one()
    db.add(ChildAgent(id=uuid.uuid4(), agent_id=agent.id, business_id=sample_business_id,
                      child_data={}, is_active=True))
    await db.flush()

    resp = await client.post(f"/api/v1/agents/{agent.id}/trial",
                             json={"business_id": str(sample_business_id), "child_data": {}},
                             headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 201, resp.text   # reused, not a duplicate-key 500
    children = (await db.execute(select(ChildAgent).where(
        ChildAgent.business_id == sample_business_id))).scalars().all()
    assert len(children) == 1                    # still exactly one


@pytest.mark.asyncio
async def test_cannot_deploy_to_unowned_business(client, db, sample_user_id, valid_access_token):
    await _seed_marketplace(db)
    other_biz = uuid.uuid4()
    db.add(User(id=sample_user_id))
    db.add(Business(id=other_biz, owner_id=uuid.uuid4(), name="Theirs", slug="theirs"))
    await db.flush()
    agent = (await db.execute(
        select(Agent).where(Agent.name == "Booking Concierge")
    )).scalar_one()

    resp = await client.post(
        f"/api/v1/agents/{agent.id}/trial",
        json={"business_id": str(other_biz), "child_data": {}},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 404   # ownership enforced
