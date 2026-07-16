"""Instant activation: one prompt seeds Brain + catalog + storefront + website."""
import json
import uuid

import pytest
from sqlalchemy import select

import app.services.business_bootstrap as bb
import app.services.storefront_ai as sai
from app.db.models import (
    Business, KnowledgeItem, KnowledgeItemType, LandingPage, MiniAppConfig, User,
)

_GOOD_LLM = json.dumps({
    "about": "A cozy hair salon in Bole serving Addis since 2018.",
    "tagline": "Look sharp, feel sharper.",
    "cta": "Book your visit",
    "hours": "Mon-Sat 9 AM - 7 PM",
    "theme": {"primary": "#2B4C3F", "accent": "#E4B363", "bg": "#FAF7F0",
              "text": "#1D1B16", "surface": "#F0EAD9",
              "font_heading": "Fraunces", "font_body": "Inter"},
    "products": [{"name": "Argan Hair Oil", "price": "450 ETB", "description": "Nourishing oil."}],
    "services": [
        {"name": "Haircut", "price": "300 ETB", "duration": "30 minutes", "description": "Classic cut."},
        {"name": "Hair Colour", "price": "1500 ETB", "duration": "2 hours", "description": "Full colour."},
    ],
    "faqs": [{"question": "Do you take Telebirr?", "answer": "Yes, Telebirr and cash."}],
    "seo": {"title": "Selam Salon — Hair Salon in Bole, Addis Ababa",
            "meta_description": "Cuts, colour and care in Bole. Book on Telegram.",
            "hero_headline": "Your neighbourhood salon",
            "hero_subheadline": "Walk-ins welcome",
            "keywords": ["salon bole", "haircut addis ababa"]},
})


def _fake_llm(monkeypatch, payload, *, boom=False):
    async def _exec(*a, **k):
        if boom:
            raise RuntimeError("provider down")
        return payload, {"input_tokens": 1, "output_tokens": 1}, "test/model"
    monkeypatch.setattr(bb.model_router, "execute_with_fallback", _exec)


async def _biz(db, owner_id=None):
    owner_id = owner_id or uuid.uuid4()
    db.add(User(id=owner_id, telegram_id=777, is_active=True))
    biz = Business(id=uuid.uuid4(), owner_id=owner_id, name="Selam Salon",
                   slug=f"s-{uuid.uuid4().hex[:8]}", category="salon")
    db.add(biz)
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_bootstrap_seeds_everything(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, _GOOD_LLM)

    out = await bb.bootstrap(db, biz, "I run a hair salon in Bole...")
    assert out["source"] == "ai"
    assert out["products"] == 1 and out["services"] == 2 and out["faqs"] == 1

    # business copy
    assert "cozy hair salon" in biz.description

    # catalog + FAQs
    items = (await db.execute(select(KnowledgeItem).where(
        KnowledgeItem.business_id == biz.id))).scalars().all()
    by_type = {}
    for i in items:
        by_type.setdefault(i.item_type, []).append(i)
    assert len(by_type[KnowledgeItemType.product]) == 1
    assert len(by_type[KnowledgeItemType.service]) == 2
    svc = next(i for i in by_type[KnowledgeItemType.service] if i.title == "Haircut")
    assert svc.data["price"] == "300 ETB" and svc.data["duration"] == "30 minutes"
    faq = by_type[KnowledgeItemType.faq][0]
    assert "Telebirr" in faq.title and "Telebirr" in faq.body

    # storefront published with theme + copy
    cfg = (await db.execute(select(MiniAppConfig).where(
        MiniAppConfig.business_id == biz.id))).scalar_one()
    assert cfg.is_published is True
    assert cfg.theme_primary == "#2b4c3f"
    assert cfg.layout_config["tagline"] == "Look sharp, feel sharper."
    assert cfg.layout_config["hours"].startswith("Mon-Sat")

    # website SEO published
    lp = (await db.execute(select(LandingPage).where(
        LandingPage.business_id == biz.id))).scalar_one()
    assert lp.is_published is True
    assert lp.title.startswith("Selam Salon")
    assert "salon bole" in lp.seo_keywords


@pytest.mark.asyncio
async def test_bootstrap_never_duplicates_existing_titles(db, monkeypatch):
    biz = await _biz(db)
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.service,
                         title="Haircut", is_active=True))
    await db.flush()
    _fake_llm(monkeypatch, _GOOD_LLM)

    out = await bb.bootstrap(db, biz, "hair salon brief")
    assert out["services"] == 1              # "Haircut" skipped, "Hair Colour" added
    titles = (await db.execute(select(KnowledgeItem.title).where(
        KnowledgeItem.business_id == biz.id))).scalars().all()
    assert titles.count("Haircut") == 1


@pytest.mark.asyncio
async def test_bootstrap_bad_llm_writes_nothing(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, "no json at all")
    out = await bb.bootstrap(db, biz, "some brief")
    assert out["source"] == "fallback"
    items = (await db.execute(select(KnowledgeItem).where(
        KnowledgeItem.business_id == biz.id))).scalars().all()
    assert items == []
    assert (await db.execute(select(MiniAppConfig).where(
        MiniAppConfig.business_id == biz.id))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_bootstrap_llm_error_is_safe(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, None, boom=True)
    out = await bb.bootstrap(db, biz, "some brief")
    assert out["source"] == "fallback"


@pytest.mark.asyncio
async def test_bootstrap_endpoint(client, db, sample_user_id, valid_access_token, monkeypatch):
    biz = await _biz(db, owner_id=sample_user_id)
    _fake_llm(monkeypatch, _GOOD_LLM)

    resp = await client.post(
        f"/api/v1/businesses/{biz.id}/bootstrap",
        json={"brief": "I run a hair salon in Bole with haircuts and colour."},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "ai"
    assert body["products"] == 1 and body["services"] == 2 and body["faqs"] == 1


@pytest.mark.asyncio
async def test_bootstrap_endpoint_owner_scoped(client, db, sample_user_id, valid_access_token, monkeypatch):
    other = uuid.uuid4()
    biz = await _biz(db, owner_id=other)
    from app.db.models import User as U
    db.add(U(id=sample_user_id))
    await db.flush()
    _fake_llm(monkeypatch, _GOOD_LLM)
    resp = await client.post(
        f"/api/v1/businesses/{biz.id}/bootstrap",
        json={"brief": "trying to write someone else's business"},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bootstrap_endpoint_rejects_tiny_brief(client, db, sample_user_id, valid_access_token):
    biz = await _biz(db, owner_id=sample_user_id)
    resp = await client.post(
        f"/api/v1/businesses/{biz.id}/bootstrap", json={"brief": "hi"},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 422
