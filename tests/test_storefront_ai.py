"""AI storefront generation: theme ("page") + copy ("content"), with
sanitisation and fail-safe fallback, plus the owner-scoped endpoint."""
import uuid

import pytest

import app.services.storefront_ai as sai
from app.db.models import Business, KnowledgeItem, KnowledgeItemType, User


def _fake_llm(monkeypatch, payload=None, *, boom=False):
    """Stub model_router.execute_with_fallback to return a fixed string."""
    async def _exec(*a, **k):
        if boom:
            raise RuntimeError("provider down")
        return payload, {"input_tokens": 1, "output_tokens": 1}, "test/model"
    monkeypatch.setattr(sai.model_router, "execute_with_fallback", _exec)


async def _biz(db, **kw):
    owner = User(id=uuid.uuid4(), telegram_id=4321, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="Selam Salon",
                   slug=f"s-{uuid.uuid4().hex[:6]}", category="salon", **kw)
    db.add(biz)
    await db.flush()
    return biz


# ── service: theme ("page") ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_page_returns_sanitized_theme(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, '{"primary":"#101820","accent":"#D4AF37","bg":"#0b0b0b",'
                           '"text":"#f5f5f5","surface":"#161616",'
                           '"font_heading":"Fraunces","font_body":"Inter"}')
    out = await sai.generate(db, biz, "page")
    assert out["kind"] == "page" and out["source"] == "ai"
    assert out["theme"]["primary"] == "#101820"
    assert out["theme"]["font_heading"] == "Fraunces"


@pytest.mark.asyncio
async def test_generate_page_rejects_bad_colors_and_fonts(db, monkeypatch):
    biz = await _biz(db)
    # invalid hex + unsupported font → those keys dropped; core trio missing → fallback
    _fake_llm(monkeypatch, '{"primary":"reddish","font_heading":"Comic Sans"}')
    out = await sai.generate(db, biz, "page")
    assert out["source"] == "fallback"
    assert out["theme"] == sai._fallback_theme()


@pytest.mark.asyncio
async def test_generate_page_fallback_on_llm_error(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, boom=True)
    out = await sai.generate(db, biz, "page")
    assert out["source"] == "fallback" and out["theme"]["primary"] == "#1a73e8"


# ── service: copy ("content") ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_content_returns_copy(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, 'Sure! {"tagline":"Look sharp, feel sharper.",'
                           '"about":"A neighbourhood barbershop with a modern edge.",'
                           '"cta":"Book your visit",'
                           '"hours":"Mon-Sat, 9 AM - 7 PM"} hope that helps')
    out = await sai.generate(db, biz, "content")
    assert out["kind"] == "content" and out["source"] == "ai"
    assert out["tagline"] == "Look sharp, feel sharper."
    assert out["about"].startswith("A neighbourhood")
    assert out["cta"] == "Book your visit"
    assert out["hours"].startswith("Mon-Sat")


@pytest.mark.asyncio
async def test_generate_content_fallback_uses_business_name(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, "no json here")
    out = await sai.generate(db, biz, "content")
    assert out["source"] == "fallback"
    assert biz.name in out["tagline"]


@pytest.mark.asyncio
async def test_catalog_titles_feed_the_prompt(db, monkeypatch):
    """The product/service catalog should be passed into the LLM context."""
    biz = await _biz(db)
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.product,
                         title="Beard Oil", is_active=True))
    await db.flush()
    seen = {}

    async def _exec(messages, **k):
        seen["user"] = messages[0]["content"]
        return '{"tagline":"x"}', {}, "m"
    monkeypatch.setattr(sai.model_router, "execute_with_fallback", _exec)
    await sai.generate(db, biz, "content", vibe="dark and moody")
    assert "Beard Oil" in seen["user"] and "dark and moody" in seen["user"]


# ── endpoint ─────────────────────────────────────────────────────────────────

# ── SEO generation ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_seo_returns_metadata(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, '{"title":"Selam Salon — Hair & Beauty in Addis",'
                           '"meta_description":"Book a cut or colour today.",'
                           '"hero_headline":"Look your best","hero_subheadline":"Walk-ins welcome",'
                           '"keywords":["addis salon","haircut addis","hair colour"]}')
    out = await sai.generate_seo(db, biz)
    assert out["source"] == "ai"
    assert out["title"].startswith("Selam Salon")
    assert out["keywords"] == ["addis salon", "haircut addis", "hair colour"]


@pytest.mark.asyncio
async def test_generate_seo_fallback(db, monkeypatch):
    biz = await _biz(db)
    _fake_llm(monkeypatch, "not json")
    out = await sai.generate_seo(db, biz)
    assert out["source"] == "fallback"
    assert biz.name in out["title"] and out["keywords"]


@pytest.mark.asyncio
async def test_website_generate_endpoint(client, db, sample_user_id, valid_access_token, monkeypatch):
    db.add(User(id=sample_user_id))
    biz = Business(id=uuid.uuid4(), owner_id=sample_user_id, name="Cafe Abol",
                   slug=f"c-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    _fake_llm(monkeypatch, '{"title":"Cafe Abol — Coffee in Addis","keywords":["addis coffee"]}')
    resp = await client.post(
        f"/api/v1/businesses/{biz.id}/website/generate", json={},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Cafe Abol — Coffee in Addis"
    assert body["keywords"] == ["addis coffee"]


@pytest.mark.asyncio
async def test_generate_endpoint(client, db, sample_user_id, valid_access_token, monkeypatch):
    db.add(User(id=sample_user_id))
    biz = Business(id=uuid.uuid4(), owner_id=sample_user_id, name="Cafe Abol",
                   slug=f"c-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    _fake_llm(monkeypatch, '{"tagline":"The taste of home."}')

    resp = await client.post(
        f"/api/v1/businesses/{biz.id}/storefront/generate",
        json={"kind": "content"},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tagline"] == "The taste of home."


@pytest.mark.asyncio
async def test_generate_endpoint_charges_wallet(client, db, sample_user_id, valid_access_token, monkeypatch):
    from app.db.models import TokenWallet, UsageEvent
    from sqlalchemy import select
    db.add(User(id=sample_user_id))
    biz = Business(id=uuid.uuid4(), owner_id=sample_user_id, name="Cafe Abol",
                   slug=f"c-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=100))
    await db.flush()
    _fake_llm(monkeypatch, '{"tagline":"The taste of home."}')

    resp = await client.post(
        f"/api/v1/businesses/{biz.id}/storefront/generate",
        json={"kind": "content"},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["charged"] == 5
    wallet = (await db.execute(select(TokenWallet).where(TokenWallet.business_id == biz.id))).scalar_one()
    assert wallet.balance == 95 and wallet.lifetime_spent == 5
    ev = (await db.execute(select(UsageEvent).where(UsageEvent.business_id == biz.id))).scalars().one()
    assert ev.action_type == "storefront_generation" and ev.etg_charged == 5


@pytest.mark.asyncio
async def test_generate_endpoint_rate_limited(client, db, mock_redis, sample_user_id, valid_access_token, monkeypatch):
    import app.api.businesses as biz_api
    monkeypatch.setattr(biz_api, "_GEN_RATE_PER_HOUR", 2)
    counter = {"n": 0}

    async def _incr(_key):
        counter["n"] += 1
        return counter["n"]
    mock_redis.incr = _incr

    db.add(User(id=sample_user_id))
    biz = Business(id=uuid.uuid4(), owner_id=sample_user_id, name="Cafe Abol",
                   slug=f"c-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    _fake_llm(monkeypatch, '{"tagline":"x"}')
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    url = f"/api/v1/businesses/{biz.id}/storefront/generate"

    assert (await client.post(url, json={"kind": "content"}, headers=hdr)).status_code == 200
    assert (await client.post(url, json={"kind": "content"}, headers=hdr)).status_code == 200
    assert (await client.post(url, json={"kind": "content"}, headers=hdr)).status_code == 429


@pytest.mark.asyncio
async def test_generate_endpoint_owner_scoped(client, db, sample_user_id, valid_access_token, monkeypatch):
    other = uuid.uuid4()
    db.add(User(id=other))
    biz = Business(id=uuid.uuid4(), owner_id=other, name="NotYours", slug=f"n-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    db.add(User(id=sample_user_id))
    await db.flush()
    _fake_llm(monkeypatch, '{"tagline":"x"}')

    resp = await client.post(
        f"/api/v1/businesses/{biz.id}/storefront/generate",
        json={"kind": "content"},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404
