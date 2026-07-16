"""Storefront location: map pin parsing + key-free directions round-trip."""
import uuid

import pytest

from app.db.models import Business, User


async def _biz(db, owner_id):
    db.add(User(id=owner_id))
    biz = Business(id=uuid.uuid4(), owner_id=owner_id, name="Selam",
                   slug=f"s-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_set_map_pin_from_latlng(client, db, sample_user_id, valid_access_token):
    biz = await _biz(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    r = await client.patch(f"/api/v1/businesses/{biz.id}/storefront",
                           json={"address": "Bole Rd", "map_pin": "9.0123, 38.7456"}, headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["address"] == "Bole Rd"
    assert body["latitude"] == 9.0123 and body["longitude"] == 38.7456
    assert body["directions_url"].endswith("query=9.0123,38.7456")


@pytest.mark.asyncio
async def test_map_pin_from_google_url(client, db, sample_user_id, valid_access_token):
    biz = await _biz(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    url = "https://www.google.com/maps/place/X/@9.0,38.0,17z/data=!3d9.0123!4d38.7456"
    r = await client.patch(f"/api/v1/businesses/{biz.id}/storefront",
                           json={"map_pin": url}, headers=hdr)
    assert r.json()["latitude"] == 9.0123 and r.json()["longitude"] == 38.7456


@pytest.mark.asyncio
async def test_address_only_still_gives_directions(client, db, sample_user_id, valid_access_token):
    biz = await _biz(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    r = await client.patch(f"/api/v1/businesses/{biz.id}/storefront",
                           json={"address": "Bole Rd, Addis Ababa"}, headers=hdr)
    assert r.json()["latitude"] is None
    assert "query=Bole" in r.json()["directions_url"]   # key-free address fallback


@pytest.mark.asyncio
async def test_clear_map_pin(client, db, sample_user_id, valid_access_token):
    biz = await _biz(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.patch(f"/api/v1/businesses/{biz.id}/storefront",
                       json={"map_pin": "9.01,38.74"}, headers=hdr)
    r = await client.patch(f"/api/v1/businesses/{biz.id}/storefront",
                           json={"map_pin": ""}, headers=hdr)
    assert r.json()["latitude"] is None and r.json()["directions_url"] is None
