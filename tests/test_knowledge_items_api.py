"""Tests for the structured-catalog (knowledge_items) CRUD API.

Owner-scoped create / list / update / delete of curated catalog rows that the
bot injects verbatim into its prompt.
"""
import uuid

import pytest
from sqlalchemy import select

import app.api.knowledge as knowledge_api
from app.db.models import Business, KnowledgeItem, KnowledgeItemType, User


async def _seed(db, user_id, business_id):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Biz", slug=f"b-{business_id.hex[:8]}"))
    await db.flush()


@pytest.fixture
def _no_gcs(monkeypatch):
    """Stub the public GCS upload so tests don't hit a real bucket."""
    async def _fake(data, path, content_type=None):
        return f"https://storage.googleapis.com/ethiogram-public/{path}"
    monkeypatch.setattr(knowledge_api.storage_service, "upload_public", _fake)


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
async def test_create_item_with_category_and_image(client, db, _no_gcs, sample_user_id,
                                                   sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    # 1. upload a product image → public URL
    up = await client.post(
        f"/api/v1/knowledge/{sample_business_id}/items/image",
        files={"file": ("dress.png", b"\x89PNG fake bytes", "image/png")},
        headers=hdr,
    )
    assert up.status_code == 201, up.text
    image_url = up.json()["image_url"]
    assert image_url.startswith("https://storage.googleapis.com/")

    # 2. create a product carrying category + image_url in data
    resp = await client.post(
        f"/api/v1/knowledge/{sample_business_id}/items",
        json={"item_type": "product", "title": "Blue Dress",
              "data": {"price": "1200 ETB", "category": "Dresses", "image_url": image_url}},
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["category"] == "Dresses"
    assert data["image_url"] == image_url

    # 3. it flows through to the public storefront (chips + product image)
    pub = (await client.get(f"/api/v1/miniapp/b-{sample_business_id.hex[:8]}")).json()["content"]
    assert pub["categories"] == ["Dresses"]
    assert pub["products"][0]["image_url"] == image_url


@pytest.mark.asyncio
async def test_image_rejects_non_image(client, db, _no_gcs, sample_user_id,
                                       sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.post(
        f"/api/v1/knowledge/{sample_business_id}/items/image",
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_image_requires_ownership(client, db, _no_gcs, sample_user_id, valid_access_token):
    other = uuid.uuid4()
    db.add(User(id=sample_user_id))
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Theirs", slug="theirs-img"))
    await db.flush()
    resp = await client.post(
        f"/api/v1/knowledge/{other}/items/image",
        files={"file": ("a.png", b"x", "image/png")},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 404


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
