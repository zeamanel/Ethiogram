"""Phase D: managing a deployed agent (ChildAgent) from the owner Mini App.

Covers the new GET /agents/child/{id} (editable config, never secrets) and the
now-partial PATCH (pause/rename without resending the whole child_data).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import encrypt_agent_prompt, encrypt_child_secrets
from app.db.models import (
    Agent,
    AgentStatus,
    AgentTrial,
    AgentUnlock,
    Business,
    ChildAgent,
    User,
)


async def _seed(db, user_id, business_id, *, child_data, child_schema=None,
                setup_guide=None, secrets=None, trial_days=None, unlocked=False):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Biz", slug=f"b-{business_id.hex[:8]}"))
    enc, key_ref = encrypt_agent_prompt("be great", str(uuid.uuid4()))
    father = Agent(
        id=uuid.uuid4(), creator_id=uuid.uuid4(), name="Booking Pro", tagline="t",
        description="d", category="Concierge & Booking", tags=[], capabilities=[],
        encrypted_system_prompt=enc, encryption_key_ref=key_ref,
        child_schema=child_schema, setup_guide=setup_guide,
        price_etg=100, status=AgentStatus.live,
    )
    db.add(father)
    await db.flush()
    child = ChildAgent(
        id=uuid.uuid4(), agent_id=father.id, business_id=business_id, is_active=True,
        display_name="My Booker", child_data=child_data,
        child_secrets=encrypt_child_secrets(secrets) if secrets else None,
    )
    db.add(child)
    await db.flush()
    if trial_days is not None:
        db.add(AgentTrial(
            agent_id=father.id, business_id=business_id, child_agent_id=child.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=trial_days),
        ))
    if unlocked:
        db.add(AgentUnlock(
            agent_id=father.id, business_id=business_id, child_agent_id=child.id,
            etg_paid=100, is_refunded=False,
        ))
    await db.flush()
    return father, child


@pytest.mark.asyncio
async def test_get_child_agent_returns_config_and_status(client, db, sample_user_id,
                                                         sample_business_id, valid_access_token):
    _, child = await _seed(
        db, sample_user_id, sample_business_id,
        child_data={"services": "Haircut", "business_hours": "9-5"},
        child_schema={"fields": [{"key": "services", "label": "Services"}]},
        setup_guide="Connect your calendar.",
        trial_days=10,
    )
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    resp = await client.get(f"/api/v1/agents/child/{child.id}", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_name"] == "Booking Pro"
    assert body["category"] == "Concierge & Booking"
    assert body["display_name"] == "My Booker"
    assert body["is_active"] is True
    assert body["status"] == "trial"
    assert body["days_left"] in (9, 10)        # ~10 days, allowing for clock
    assert body["child_data"]["services"] == "Haircut"
    assert body["child_schema"]["fields"][0]["key"] == "services"
    assert body["setup_guide"] == "Connect your calendar."
    assert body["has_secrets"] is False


@pytest.mark.asyncio
async def test_get_child_agent_never_returns_secret_values(client, db, sample_user_id,
                                                           sample_business_id, valid_access_token):
    _, child = await _seed(
        db, sample_user_id, sample_business_id,
        child_data={"services": "Consult"},
        secrets={"calendar_id": "cal@x.com", "credentials_json": '{"token":"SENSITIVE"}'},
        unlocked=True,
    )
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    resp = await client.get(f"/api/v1/agents/child/{child.id}", headers=hdr)
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_secrets"] is True
    assert body["status"] == "unlocked"
    assert body["days_left"] is None
    # the secret must NEVER appear anywhere in the payload
    assert "SENSITIVE" not in resp.text
    assert "credentials_json" not in resp.text
    assert "cal@x.com" not in resp.text


@pytest.mark.asyncio
async def test_patch_pause_without_resending_child_data(client, db, sample_user_id,
                                                        sample_business_id, valid_access_token):
    _, child = await _seed(
        db, sample_user_id, sample_business_id,
        child_data={"services": "Haircut", "business_hours": "9-5"},
    )
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    # partial update: only is_active — child_data must be preserved
    resp = await client.patch(f"/api/v1/agents/child/{child.id}",
                              json={"is_active": False}, headers=hdr)
    assert resp.status_code == 204, resp.text

    after = (await client.get(f"/api/v1/agents/child/{child.id}", headers=hdr)).json()
    assert after["is_active"] is False
    assert after["child_data"] == {"services": "Haircut", "business_hours": "9-5"}  # untouched


@pytest.mark.asyncio
async def test_patch_rename_and_config(client, db, sample_user_id,
                                       sample_business_id, valid_access_token):
    _, child = await _seed(db, sample_user_id, sample_business_id,
                           child_data={"services": "Old"})
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    resp = await client.patch(
        f"/api/v1/agents/child/{child.id}",
        json={"display_name": "Front Desk", "child_data": {"services": "New, Spa"}},
        headers=hdr,
    )
    assert resp.status_code == 204
    after = (await client.get(f"/api/v1/agents/child/{child.id}", headers=hdr)).json()
    assert after["display_name"] == "Front Desk"
    assert after["child_data"] == {"services": "New, Spa"}


@pytest.mark.asyncio
async def test_cannot_manage_unowned_child_agent(client, db, sample_user_id, valid_access_token):
    other_owner = uuid.uuid4()
    biz = uuid.uuid4()
    _, child = await _seed(db, other_owner, biz, child_data={"services": "X"})
    # sample_user_id (the token holder) does not own this business
    db.add(User(id=sample_user_id))
    await db.flush()
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    assert (await client.get(f"/api/v1/agents/child/{child.id}", headers=hdr)).status_code == 404
    patch = await client.patch(f"/api/v1/agents/child/{child.id}",
                               json={"is_active": False}, headers=hdr)
    assert patch.status_code == 404


@pytest.mark.asyncio
async def test_patch_secrets_round_trip_and_never_leak(client, db, sample_user_id,
                                                       sample_business_id, valid_access_token):
    from app.core.security import decrypt_child_secrets
    _, child = await _seed(db, sample_user_id, sample_business_id,
                           child_data={"services": "Consult"})
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    # connect credentials via the write-only secrets editor path
    creds = {"calendar_id": "cal@x.com", "credentials_json": '{"token":"SENSITIVE"}'}
    resp = await client.patch(f"/api/v1/agents/child/{child.id}",
                              json={"child_secrets": creds}, headers=hdr)
    assert resp.status_code == 204

    # stored encrypted, decrypts back to exactly what we sent (the agent will read it).
    # The endpoint mutates the same in-session object — read directly (refresh would
    # revert the not-yet-committed change).
    assert child.child_secrets is not None
    assert decrypt_child_secrets(child.child_secrets) == creds

    # GET reports connected but NEVER returns the secret values
    got = await client.get(f"/api/v1/agents/child/{child.id}", headers=hdr)
    assert got.json()["has_secrets"] is True
    assert "SENSITIVE" not in got.text
    assert "cal@x.com" not in got.text


@pytest.mark.asyncio
async def test_patch_without_secrets_keeps_existing(client, db, sample_user_id,
                                                    sample_business_id, valid_access_token):
    from app.core.security import decrypt_child_secrets
    _, child = await _seed(db, sample_user_id, sample_business_id,
                           child_data={"services": "X"},
                           secrets={"calendar_id": "keep@x.com", "credentials_json": "{}"})
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    # a save that omits child_secrets (blank editor) must NOT wipe credentials
    resp = await client.patch(f"/api/v1/agents/child/{child.id}",
                              json={"display_name": "Renamed"}, headers=hdr)
    assert resp.status_code == 204
    # child_secrets untouched (read directly — same in-session object)
    assert decrypt_child_secrets(child.child_secrets)["calendar_id"] == "keep@x.com"
