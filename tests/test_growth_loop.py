"""Growth loop: referral capture/redeem, powered-by links, public directory."""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Business, LandingPage, TokenWallet, User
from app.services import referral_service


class _FakeRedis:
    """Dict-backed stand-in — conftest's fredis is stateless (get→None)."""
    def __init__(self):
        self.store = {}
    async def get(self, k): return self.store.get(k)
    async def set(self, k, v, ex=None): self.store[k] = str(v); return True
    async def setex(self, k, ttl, v): self.store[k] = str(v); return True
    async def delete(self, *ks):
        for k in ks: self.store.pop(k, None)
        return 1


@pytest.fixture
def fredis():
    return _FakeRedis()


# ── referral capture ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_stores_pending_referral(fredis):
    assert await referral_service.capture(fredis, "111", "222") is True
    assert await fredis.get("ref:pending:111") == "222"


@pytest.mark.asyncio
async def test_capture_rejects_self_and_garbage(fredis):
    assert await referral_service.capture(fredis, "111", "111") is False   # self
    assert await referral_service.capture(fredis, "111", "abc") is False   # not an id
    assert await referral_service.capture(fredis, "", "222") is False


@pytest.mark.asyncio
async def test_first_referrer_wins(fredis):
    await referral_service.capture(fredis, "111", "222")
    assert await referral_service.capture(fredis, "111", "333") is False
    assert await fredis.get("ref:pending:111") == "222"


# ── referral redemption ──────────────────────────────────────────────────────

async def _owner_with_business(db, tg_id):
    owner = User(id=uuid.uuid4(), telegram_id=tg_id, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name=f"B{tg_id}",
                   slug=f"b-{uuid.uuid4().hex[:8]}")
    db.add(biz)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=0))
    await db.flush()
    return owner, biz


@pytest.mark.asyncio
async def test_redeem_credits_both_sides(db, fredis):
    referrer, referrer_biz = await _owner_with_business(db, 222)
    new_owner, new_biz = await _owner_with_business(db, 111)
    await referral_service.capture(fredis, "111", "222")

    assert await referral_service.redeem(db, fredis, new_biz.id, 111) is True

    from app.core.config import settings
    w_new = (await db.execute(select(TokenWallet).where(
        TokenWallet.business_id == new_biz.id))).scalar_one()
    w_ref = (await db.execute(select(TokenWallet).where(
        TokenWallet.business_id == referrer_biz.id))).scalar_one()
    assert w_new.balance == settings.etg_referral_bonus
    assert w_ref.balance == settings.etg_referral_bonus

    # key consumed → a second redemption is a no-op
    assert await referral_service.redeem(db, fredis, new_biz.id, 111) is False
    assert (await db.execute(select(TokenWallet).where(
        TokenWallet.business_id == new_biz.id))).scalar_one().balance == settings.etg_referral_bonus


@pytest.mark.asyncio
async def test_redeem_no_pending_is_noop(db, fredis):
    _, biz = await _owner_with_business(db, 444)
    assert await referral_service.redeem(db, fredis, biz.id, 444) is False


@pytest.mark.asyncio
async def test_redeem_survives_unknown_referrer(db, fredis):
    """Referrer deleted their account → new business still gets its bonus."""
    _, new_biz = await _owner_with_business(db, 555)
    await referral_service.capture(fredis, "555", "999999")   # no such user
    assert await referral_service.redeem(db, fredis, new_biz.id, 555) is True
    from app.core.config import settings
    w = (await db.execute(select(TokenWallet).where(
        TokenWallet.business_id == new_biz.id))).scalar_one()
    assert w.balance == settings.etg_referral_bonus


# ── auth exposes the referral link ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_auth_response_model_has_referral_fields():
    from app.api.auth import TokenResponse
    fields = TokenResponse.model_fields
    assert "referral_link" in fields and "referral_bonus" in fields


# ── public directory + powered-by ────────────────────────────────────────────

async def _published_business(db, name, category):
    owner = User(id=uuid.uuid4(), telegram_id=hash(name) % 10**9, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name=name,
                   slug=f"d-{uuid.uuid4().hex[:8]}", category=category,
                   description=f"{name} description")
    db.add(biz)
    db.add(LandingPage(business_id=biz.id, is_published=True))
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_directory_lists_published_businesses(client, db):
    biz = await _published_business(db, "Selam Salon", "salon")
    resp = await client.get("/directory")
    assert resp.status_code == 200
    body = resp.text
    assert "Selam Salon" in body
    assert f"/biz/{biz.slug}" in body
    assert "Salon" in body                       # category heading
    assert "Get an AI bot" in body               # acquisition CTA


@pytest.mark.asyncio
async def test_directory_in_sitemap(client, db):
    await _published_business(db, "Cafe Abol", "cafe")
    xml = (await client.get("/sitemap.xml")).text
    assert "/directory" in xml


@pytest.mark.asyncio
async def test_landing_footer_links_to_ethiogram(client, db):
    biz = await _published_business(db, "Muya Crafts", "crafts")
    resp = await client.get(f"/biz/{biz.slug}")
    assert resp.status_code == 200
    assert "Powered by Ethiogram" in resp.text
    assert f"?start=web_{biz.slug}" in resp.text   # attributable signup link
