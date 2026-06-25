"""Tests for the public customer-storefront API: GET /miniapp/{slug}.

One data-driven engine — assembles theme + layout + content from the business,
its mini_app_config, and its knowledge_items. Public (no auth). Cached in Redis,
busted on catalog changes.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    Bot,
    BotStatus,
    Business,
    KnowledgeItem,
    KnowledgeItemType,
    MiniAppConfig,
    User,
)


async def _biz(db, *, slug="selam-store", name="Selam Store", deleted=False):
    user_id = uuid.uuid4()
    db.add(User(id=user_id))
    biz = Business(
        id=uuid.uuid4(), owner_id=user_id, name=name, slug=slug,
        description="Best store in town", category="Fashion",
        phone="+251911000000", address="Bole, Addis Ababa", currency="ETB",
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(biz)
    await db.flush()
    return biz


async def _item(db, biz_id, item_type, title, body=None, data=None):
    it = KnowledgeItem(id=uuid.uuid4(), business_id=biz_id, item_type=item_type,
                       title=title, body=body, data=data, is_active=True)
    db.add(it)
    await db.flush()
    return it


@pytest.mark.asyncio
async def test_storefront_public_no_auth_required(client, db):
    biz = await _biz(db)
    # NOTE: no Authorization header — this is a public catalog
    resp = await client.get(f"/api/v1/miniapp/{biz.slug}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["business"]["name"] == "Selam Store"
    assert body["business"]["slug"] == "selam-store"
    assert set(body.keys()) == {"business", "theme", "layout", "content"}


@pytest.mark.asyncio
async def test_storefront_assembles_content_from_knowledge_items(client, db):
    biz = await _biz(db)
    await _item(db, biz.id, KnowledgeItemType.product, "Blue Dress",
                body="Cotton", data={"price": "1200 ETB", "category": "Dresses"})
    await _item(db, biz.id, KnowledgeItemType.product, "Red Shoes",
                data={"price": "800 ETB", "category": "Shoes"})
    await _item(db, biz.id, KnowledgeItemType.service, "Tailoring",
                data={"price": "300 ETB", "duration": "2 days"})
    await _item(db, biz.id, KnowledgeItemType.menu_item, "Macchiato",
                data={"price": "60 ETB", "category": "Drinks"})
    await _item(db, biz.id, KnowledgeItemType.faq, "Do you deliver?", body="Yes, free over 1000 ETB.")

    c = (await client.get(f"/api/v1/miniapp/{biz.slug}")).json()["content"]
    assert {p["title"] for p in c["products"]} == {"Blue Dress", "Red Shoes"}
    assert c["categories"] == ["Dresses", "Shoes"]            # derived from product data
    assert c["services"][0]["duration"] == "2 days"
    assert c["menu"][0]["category"] == "Drinks"               # grouped
    assert c["faqs"][0] == {"q": "Do you deliver?", "a": "Yes, free over 1000 ETB."}


@pytest.mark.asyncio
async def test_storefront_theme_defaults_and_overrides(client, db):
    biz = await _biz(db, slug="themed")
    db.add(MiniAppConfig(
        business_id=biz.id, theme_primary="#112233", theme_accent="#445566",
        font_family="Poppins",
        layout_config={"theme_overrides": {"bg": "#000000", "radius": "24px"},
                       "tagline": "Fresh daily"},
    ))
    await db.flush()
    body = (await client.get(f"/api/v1/miniapp/themed")).json()
    theme = body["theme"]
    assert theme["primary"] == "#112233"      # from config column
    assert theme["accent"] == "#445566"
    assert theme["font_body"] == "Poppins"
    assert theme["bg"] == "#000000"           # JSONB override applied over default
    assert theme["radius"] == "24px"
    assert body["business"]["tagline"] == "Fresh daily"


@pytest.mark.asyncio
async def test_storefront_surfaces_active_bot_username(client, db):
    biz = await _biz(db, slug="withbot")
    db.add(Bot(id=uuid.uuid4(), business_id=biz.id, bot_username="selam_bot",
               encrypted_token="x", token_hash=uuid.uuid4().hex, status=BotStatus.active))
    await db.flush()
    body = (await client.get(f"/api/v1/miniapp/withbot")).json()
    assert body["business"]["bot_username"] == "selam_bot"
    assert body["content"]["contact"]["bot_username"] == "selam_bot"


@pytest.mark.asyncio
async def test_storefront_default_layout_sections(client, db):
    biz = await _biz(db, slug="defaults")
    sections = (await client.get(f"/api/v1/miniapp/defaults")).json()["layout"]["sections"]
    types = [s["type"] for s in sections]
    assert "hero" in types and "products" in types and "chat" in types
    assert types == sorted(types, key=lambda t: [s["order"] for s in sections if s["type"] == t][0])


@pytest.mark.asyncio
async def test_storefront_404_for_unknown_slug(client, db):
    resp = await client.get("/api/v1/miniapp/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_storefront_404_for_deleted_business(client, db):
    biz = await _biz(db, slug="gone", deleted=True)
    resp = await client.get(f"/api/v1/miniapp/gone")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_storefront_served_from_cache_when_present(client, db, mock_redis):
    biz = await _biz(db, slug="cached")
    cached_payload = {"business": {"name": "FROM CACHE"}, "theme": {}, "layout": {}, "content": {}}
    mock_redis.get.return_value = json.dumps(cached_payload)
    body = (await client.get(f"/api/v1/miniapp/cached")).json()
    assert body["business"]["name"] == "FROM CACHE"   # DB not consulted


@pytest.mark.asyncio
async def test_catalog_change_busts_storefront_cache(client, db, mock_redis,
                                                     sample_user_id, sample_business_id,
                                                     valid_access_token):
    # owner-owned business so the authed catalog endpoint passes
    db.add(User(id=sample_user_id))
    db.add(Business(id=sample_business_id, owner_id=sample_user_id, name="B", slug="b-cache"))
    await db.flush()
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    mock_redis.delete.reset_mock()
    resp = await client.post(f"/api/v1/knowledge/{sample_business_id}/items",
                             json={"item_type": "product", "title": "X"}, headers=hdr)
    assert resp.status_code == 201
    # the storefront cache for this business's slug was invalidated
    mock_redis.delete.assert_awaited()
