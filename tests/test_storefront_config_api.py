"""Tests for the owner-side storefront config editor:
GET/PATCH /businesses/{id}/storefront. Upserts MiniAppConfig, round-trips with
the public storefront engine, and busts its cache.
"""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Business, MiniAppConfig, User


async def _seed(db, user_id, business_id, slug="acme"):
    db.add(User(id=user_id))
    db.add(Business(id=business_id, owner_id=user_id, name="Acme", slug=slug,
                    description="We sell things"))
    await db.flush()


@pytest.mark.asyncio
async def test_get_defaults_when_no_config(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.get(f"/api/v1/businesses/{sample_business_id}/storefront",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_published"] is False
    assert body["store_url"] == "/app/store/?s=acme"
    assert body["tagline"] == "We sell things"          # falls back to description
    # all known section types are present and editable
    types = {s["type"] for s in body["sections"]}
    assert {"hero", "products", "services", "menu", "contact", "chat"} <= types
    assert all("label" in s for s in body["sections"])


@pytest.mark.asyncio
async def test_patch_theme_and_sections_persist(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    resp = await client.patch(
        f"/api/v1/businesses/{sample_business_id}/storefront",
        json={
            "theme": {"primary": "#112233", "accent": "#445566", "bg": "#000000"},
            "tagline": "Fresh daily",
            "hours": "Mon-Sat 9-6",
            "is_published": True,
            "sections": [
                {"type": "hero", "visible": True, "order": 0},
                {"type": "products", "visible": True, "order": 1},
                {"type": "menu", "visible": False, "order": 2},
            ],
        },
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["theme"]["primary"] == "#112233"
    assert body["theme"]["bg"] == "#000000"            # stored as override, reflected back
    assert body["tagline"] == "Fresh daily"
    assert body["hours"] == "Mon-Sat 9-6"
    assert body["is_published"] is True

    # persisted on the model
    cfg = (await db.execute(select(MiniAppConfig).where(
        MiniAppConfig.business_id == sample_business_id))).scalar_one()
    assert cfg.theme_primary == "#112233"
    assert cfg.layout_config["theme_overrides"]["bg"] == "#000000"
    assert cfg.layout_config["hours"] == "Mon-Sat 9-6"
    assert cfg.is_published is True and cfg.published_at is not None


@pytest.mark.asyncio
async def test_logo_persists_and_shows_in_storefront(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id, slug="logo")
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    url = "https://storage.googleapis.com/ethiogram-public/logos/x.png"

    resp = await client.patch(f"/api/v1/businesses/{sample_business_id}/storefront",
                              json={"logo_url": url}, headers=hdr)
    assert resp.status_code == 200
    assert resp.json()["logo_url"] == url

    # persisted on the Business row (not the JSONB config)
    biz = (await db.execute(select(Business).where(Business.id == sample_business_id))).scalar_one()
    assert biz.logo_url == url

    # surfaces in the public storefront payload (store header renders it)
    pub = (await client.get("/api/v1/miniapp/logo")).json()
    assert pub["business"]["logo_url"] == url

    # GET reflects it; clearing with "" removes it
    assert (await client.get(f"/api/v1/businesses/{sample_business_id}/storefront",
                             headers=hdr)).json()["logo_url"] == url
    cleared = await client.patch(f"/api/v1/businesses/{sample_business_id}/storefront",
                                 json={"logo_url": ""}, headers=hdr)
    assert cleared.json()["logo_url"] is None


@pytest.mark.asyncio
async def test_fonts_and_store_vibe_persist(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id, slug="vibe")
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    resp = await client.patch(
        f"/api/v1/businesses/{sample_business_id}/storefront",
        json={"font_heading": "Fraunces", "font_body": "Inter",
              "ui_child_prompt": "Make it feel like a luxury boutique — dark, warm, gold."},
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    b = resp.json()
    assert b["font_heading"] == "Fraunces" and b["font_body"] == "Inter"
    assert "luxury boutique" in b["ui_child_prompt"]

    cfg = (await db.execute(select(MiniAppConfig).where(
        MiniAppConfig.business_id == sample_business_id))).scalar_one()
    assert cfg.font_heading == "Fraunces" and cfg.font_body == "Inter"
    assert cfg.ui_child_prompt.startswith("Make it feel")

    # fonts flow to the public store theme
    pub = (await client.get("/api/v1/miniapp/vibe")).json()
    assert pub["theme"]["font_heading"] == "Fraunces"
    assert pub["theme"]["font_body"] == "Inter"


@pytest.mark.asyncio
async def test_patch_round_trips_with_public_storefront(client, db, sample_user_id, sample_business_id, valid_access_token):
    """What the owner saves is what the public storefront serves."""
    await _seed(db, sample_user_id, sample_business_id, slug="round")
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.patch(
        f"/api/v1/businesses/{sample_business_id}/storefront",
        json={"theme": {"primary": "#ABCDEF"}, "tagline": "Hi there",
              "sections": [{"type": "hero", "visible": True, "order": 0},
                           {"type": "contact", "visible": True, "order": 1}]},
        headers=hdr,
    )
    pub = (await client.get("/api/v1/miniapp/round")).json()
    assert pub["theme"]["primary"] == "#ABCDEF"
    assert pub["business"]["tagline"] == "Hi there"
    assert [s["type"] for s in pub["layout"]["sections"]] == ["hero", "contact"]


@pytest.mark.asyncio
async def test_patch_busts_storefront_cache(client, db, mock_redis, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id, slug="bust")
    mock_redis.delete.reset_mock()
    resp = await client.patch(f"/api/v1/businesses/{sample_business_id}/storefront",
                              json={"is_published": True},
                              headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200
    mock_redis.delete.assert_awaited()


@pytest.mark.asyncio
async def test_invalid_hex_color_rejected(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    resp = await client.patch(f"/api/v1/businesses/{sample_business_id}/storefront",
                              json={"theme": {"primary": "not-a-color"}},
                              headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cannot_edit_unowned_storefront(client, db, sample_user_id, valid_access_token):
    other = uuid.uuid4()
    db.add(User(id=sample_user_id))
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Theirs", slug="theirs"))
    await db.flush()
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    assert (await client.get(f"/api/v1/businesses/{other}/storefront", headers=hdr)).status_code == 404
    assert (await client.patch(f"/api/v1/businesses/{other}/storefront",
                               json={"is_published": True}, headers=hdr)).status_code == 404
