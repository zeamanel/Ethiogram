"""Tests for the owner-side Website (SEO landing) editor:
GET/PATCH /businesses/{id}/website. Upserts LandingPage, round-trips with the
public /biz/{slug} page, busts its cache.
"""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Business, LandingPage, User


async def _seed(db, user_id, business_id, slug="acme"):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Acme", slug=slug,
                    description="We sell things"))
    await db.flush()


@pytest.mark.asyncio
async def test_get_defaults_when_no_landing_page(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.get(f"/api/v1/businesses/{sample_business_id}/website",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_published"] is False
    assert body["website_url"] == "/biz/acme"
    assert body["seo_keywords"] == []
    assert body["title"] is None


@pytest.mark.asyncio
async def test_patch_persists_seo_fields(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    resp = await client.patch(
        f"/api/v1/businesses/{sample_business_id}/website",
        json={"title": "Acme — Best Shop in Addis", "meta_description": "Quality goods, fast delivery.",
              "hero_headline": "Shop the best", "seo_keywords": ["addis", "shop"],
              "og_image_url": "https://img/og.png", "is_published": True},
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Acme — Best Shop in Addis"
    assert body["seo_keywords"] == ["addis", "shop"]
    assert body["is_published"] is True

    lp = (await db.execute(select(LandingPage).where(
        LandingPage.business_id == sample_business_id))).scalar_one()
    assert lp.hero_headline == "Shop the best"
    assert lp.is_published is True and lp.published_at is not None


@pytest.mark.asyncio
async def test_website_round_trips_with_public_page(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id, slug="round")
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.patch(
        f"/api/v1/businesses/{sample_business_id}/website",
        json={"title": "Round SEO Title", "meta_description": "Meta from editor",
              "hero_headline": "Big headline here", "is_published": True},
        headers=hdr,
    )
    html = (await client.get("/biz/round")).text
    assert "<title>Round SEO Title</title>" in html
    assert 'content="Meta from editor"' in html
    assert "Big headline here" in html
    assert 'content="noindex"' not in html        # published → indexable


@pytest.mark.asyncio
async def test_patch_busts_landing_cache(client, db, mock_redis, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id, slug="bust")
    mock_redis.delete.reset_mock()
    resp = await client.patch(f"/api/v1/businesses/{sample_business_id}/website",
                              json={"is_published": True},
                              headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200
    # both storefront + landing keys dropped
    busted = {c.args[0] for c in mock_redis.delete.await_args_list}
    assert any("landing:html:bust" == k for k in busted)


@pytest.mark.asyncio
async def test_title_too_long_rejected(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.patch(f"/api/v1/businesses/{sample_business_id}/website",
                              json={"title": "x" * 71},
                              headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cannot_edit_unowned_website(client, db, sample_user_id, valid_access_token):
    other = uuid.uuid4()
    db.add(User(id=sample_user_id))
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Theirs", slug="theirs-w"))
    await db.flush()
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    assert (await client.get(f"/api/v1/businesses/{other}/website", headers=hdr)).status_code == 404
    assert (await client.patch(f"/api/v1/businesses/{other}/website",
                               json={"is_published": True}, headers=hdr)).status_code == 404
