"""Suspending a business hides it from the owner and blocks access everywhere."""
import uuid
from datetime import datetime, timezone

import pytest

from app.db.models import Business, KnowledgeItem, KnowledgeItemType, User, UserRole


async def _owned(db, user_id, *, slug="acme", suspended=False):
    biz = Business(id=uuid.uuid4(), owner_id=user_id, name="Acme", slug=slug,
                   description="x", is_suspended=suspended)
    db.add(biz)
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_suspended_hidden_from_owner_dashboard(client, db, sample_user_id, valid_access_token):
    db.add(User(id=sample_user_id, role=UserRole.owner, is_active=True))
    await _owned(db, sample_user_id, slug="live")
    await _owned(db, sample_user_id, slug="susp", suspended=True)
    await db.flush()

    listing = (await client.get("/api/v1/dashboard/businesses",
                                headers={"Authorization": f"Bearer {valid_access_token}"})).json()
    slugs = {b["slug"] for b in listing}
    assert "live" in slugs and "susp" not in slugs   # suspended hidden


@pytest.mark.asyncio
async def test_suspended_blocks_owner_overview(client, db, sample_user_id, valid_access_token):
    db.add(User(id=sample_user_id, role=UserRole.owner, is_active=True))
    biz = await _owned(db, sample_user_id, slug="blocked", suspended=True)
    await db.flush()
    resp = await client.get(f"/api/v1/dashboard/overview/{biz.id}",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404   # ownership helper treats suspended as not-found


@pytest.mark.asyncio
async def test_suspended_blocks_catalog_management(client, db, sample_user_id, valid_access_token):
    db.add(User(id=sample_user_id, role=UserRole.owner, is_active=True))
    biz = await _owned(db, sample_user_id, slug="cat", suspended=True)
    await db.flush()
    resp = await client.post(f"/api/v1/knowledge/{biz.id}/items",
                             json={"item_type": "product", "title": "X"},
                             headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_suspended_public_store_and_site_go_dark(client, db):
    uid = uuid.uuid4()
    db.add(User(id=uid, role=UserRole.owner, is_active=True))
    biz = await _owned(db, uid, slug="dark", suspended=True)
    db.add(KnowledgeItem(id=uuid.uuid4(), business_id=biz.id, item_type=KnowledgeItemType.product,
                         title="Hidden", is_active=True))
    await db.flush()
    assert (await client.get("/api/v1/miniapp/dark")).status_code == 404   # storefront
    assert (await client.get("/biz/dark")).status_code == 404              # SEO site


@pytest.mark.asyncio
async def test_active_business_still_works(client, db, sample_user_id, valid_access_token):
    db.add(User(id=sample_user_id, role=UserRole.owner, is_active=True))
    biz = await _owned(db, sample_user_id, slug="ok")
    await db.flush()
    # owner overview reachable, public store reachable
    assert (await client.get(f"/api/v1/dashboard/overview/{biz.id}",
            headers={"Authorization": f"Bearer {valid_access_token}"})).status_code == 200
    assert (await client.get("/api/v1/miniapp/ok")).status_code == 200
