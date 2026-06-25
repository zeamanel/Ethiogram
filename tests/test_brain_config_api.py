"""Tests for the Business Brain settings editor (GET/PATCH /businesses/{id}/brain)."""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Business, BusinessBrainConfig, User


async def _seed(db, user_id, business_id, *, with_brain=False):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Selam", slug=f"s{business_id.hex[:6]}"))
    if with_brain:
        db.add(BusinessBrainConfig(business_id=business_id, persona_name="Selam", persona_tone="warm"))
    await db.flush()


@pytest.mark.asyncio
async def test_get_brain_returns_defaults_when_none(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)   # no brain config
    resp = await client.get(f"/api/v1/businesses/{sample_business_id}/brain",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rag_similarity_threshold"] == 0.3        # the new default
    assert body["persona_tone"] == "friendly"


@pytest.mark.asyncio
async def test_patch_creates_config_when_none(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.patch(
        f"/api/v1/businesses/{sample_business_id}/brain",
        json={"persona_name": "Hana", "rag_similarity_threshold": 0.4},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["persona_name"] == "Hana"
    row = (await db.execute(select(BusinessBrainConfig))).scalars().one()
    assert row.persona_name == "Hana"
    assert row.rag_similarity_threshold == 0.4


@pytest.mark.asyncio
async def test_patch_updates_only_given_fields(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id, with_brain=True)
    resp = await client.patch(
        f"/api/v1/businesses/{sample_business_id}/brain",
        json={"rag_similarity_threshold": 0.5},     # only threshold
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rag_similarity_threshold"] == 0.5
    assert body["persona_name"] == "Selam"          # unchanged
    assert body["persona_tone"] == "warm"


@pytest.mark.asyncio
async def test_threshold_out_of_range_rejected(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id, with_brain=True)
    resp = await client.patch(
        f"/api/v1/businesses/{sample_business_id}/brain",
        json={"rag_similarity_threshold": 1.5},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cannot_edit_unowned_business(client, db, sample_user_id, valid_access_token):
    db.add(User(id=sample_user_id))
    other = uuid.uuid4()
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Theirs", slug="theirs"))
    await db.flush()
    resp = await client.patch(f"/api/v1/businesses/{other}/brain",
                              json={"persona_name": "X"},
                              headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404
