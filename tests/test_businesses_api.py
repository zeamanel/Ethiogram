"""Tests for POST /businesses (onboarding step 1: create a business)."""
import pytest
from sqlalchemy import select

from app.db.models import Business, User


async def _seed_user(db, user_id):
    db.add(User(id=user_id))
    await db.flush()


@pytest.mark.asyncio
async def test_create_business(client, db, sample_user_id, valid_access_token):
    await _seed_user(db, sample_user_id)
    resp = await client.post(
        "/api/v1/businesses",
        json={"name": "Selam Store", "category": "retail"},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Selam Store"
    assert body["slug"] == "selam-store"
    assert body["category"] == "retail"

    row = (await db.execute(select(Business))).scalars().one()
    assert row.owner_id == sample_user_id
    assert row.country_code == "ET"           # model default applied
    assert row.timezone == "Africa/Addis_Ababa"


@pytest.mark.asyncio
async def test_duplicate_name_gets_unique_slug(client, db, sample_user_id, valid_access_token):
    await _seed_user(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    a = await client.post("/api/v1/businesses", json={"name": "Cafe Abol"}, headers=hdr)
    b = await client.post("/api/v1/businesses", json={"name": "Cafe Abol"}, headers=hdr)
    assert a.json()["slug"] == "cafe-abol"
    assert b.json()["slug"] == "cafe-abol-2"     # de-duplicated


@pytest.mark.asyncio
async def test_short_name_rejected(client, db, sample_user_id, valid_access_token):
    await _seed_user(db, sample_user_id)
    resp = await client.post("/api/v1/businesses", json={"name": "x"},
                             headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client):
    resp = await client.post("/api/v1/businesses", json={"name": "No Auth"})
    assert resp.status_code in (401, 403)
