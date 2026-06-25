"""Tests for the structured-catalog (knowledge_items) CRUD API.

Owner-scoped create / list / update / delete of curated catalog rows that the
bot injects verbatim into its prompt.
"""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Business, KnowledgeItem, KnowledgeItemType, User


async def _seed(db, user_id, business_id):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Biz", slug=f"b-{business_id.hex[:8]}"))
    await db.flush()


@pytest.mark.asyncio
async def test_create_and_list_item(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    resp = await client.post(
        f"/api/v1/knowledge/{sample_business_id}/items",
        json={"item_type": "product", "title": "Blue Dress",
              "body": "Cotton summer dress", "data": {"price": "1200 ETB"}},
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["item_type"] == "product"
    assert body["title"] == "Blue Dress"
    assert body["data"]["price"] == "1200 ETB"
    assert body["is_active"] is True

    # persisted
    row = (await db.execute(select(KnowledgeItem))).scalars().one()
    assert row.item_type == KnowledgeItemType.product
    assert row.business_id == sample_business_id

    listed = await client.get(f"/api/v1/knowledge/{sample_business_id}/items", headers=hdr)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_update_item(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    created = (await client.post(
        f"/api/v1/knowledge/{sample_business_id}/items",
        json={"item_type": "product", "title": "Old", "data": {"price": "10"}},
        headers=hdr,
    )).json()

    resp = await client.patch(
        f"/api/v1/knowledge/{sample_business_id}/items/{created['id']}",
        json={"title": "New", "is_active": False},
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "New"
    assert body["is_active"] is False
    assert body["data"]["price"] == "10"   # untouched field preserved


@pytest.mark.asyncio
async def test_delete_item(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    created = (await client.post(
        f"/api/v1/knowledge/{sample_business_id}/items",
        json={"item_type": "faq", "title": "Q?"}, headers=hdr,
    )).json()

    resp = await client.delete(
        f"/api/v1/knowledge/{sample_business_id}/items/{created['id']}", headers=hdr)
    assert resp.status_code == 204
    assert (await db.execute(select(KnowledgeItem))).first() is None


@pytest.mark.asyncio
async def test_invalid_item_type_rejected(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.post(
        f"/api/v1/knowledge/{sample_business_id}/items",
        json={"item_type": "weapon", "title": "X"},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 422
    assert (await db.execute(select(KnowledgeItem))).first() is None


@pytest.mark.asyncio
async def test_cannot_touch_unowned_business_items(client, db, sample_user_id, valid_access_token):
    db.add(User(id=sample_user_id))
    other = uuid.uuid4()
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Theirs", slug="theirs"))
    await db.flush()

    resp = await client.post(
        f"/api/v1/knowledge/{other}/items",
        json={"item_type": "product", "title": "X"},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 404
