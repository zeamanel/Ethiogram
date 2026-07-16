"""Chapa recharge: initialize → checkout URL, and a verified webhook credits ETG."""
import hashlib
import hmac
import uuid

import pytest
from sqlalchemy import select

import app.api.billing as billing
import app.services.chapa_service as cs
from app.db.models import (
    Business, EtgPackage, PaymentStatus, RechargeOrder, TokenWallet, User,
)


# ── chapa_service (httpx mocked) ─────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _mock_httpx(monkeypatch, *, post=None, get=None):
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k): return post
        async def get(self, url, **k): return get
    monkeypatch.setattr(cs.httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_initialize_returns_checkout_url(monkeypatch):
    monkeypatch.setattr(cs.settings, "chapa_secret_key", "CHASECK_TEST-x")
    monkeypatch.setattr(cs, "LULIT_BASE_URL", "https://lulit.example")
    _mock_httpx(monkeypatch, post=_Resp(200, {"checkout_url": "https://checkout.chapa.co/abc"}))
    url = await cs.chapa_service.initialize(
        amount=100, currency="ETB", tx_ref="etg-1", email="a@x.com",
        first_name="Abebe", callback_url="https://api/cb")
    assert url == "https://checkout.chapa.co/abc"


@pytest.mark.asyncio
async def test_initialize_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(cs.settings, "chapa_secret_key", None)
    monkeypatch.setattr(cs, "LULIT_BASE_URL", "")
    url = await cs.chapa_service.initialize(
        amount=100, currency="ETB", tx_ref="x", email=None, first_name=None, callback_url="cb")
    assert url is None


@pytest.mark.asyncio
async def test_initialize_uses_lulit_endpoint(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            captured["url"] = url
            captured["json"] = k["json"]
            return _Resp(200, {"checkout_url": "https://lulit.example/checkout"})

    monkeypatch.setattr(cs.httpx, "AsyncClient", _Client)
    url = await cs.chapa_service.initialize(
        amount=100, currency="ETB", tx_ref="etg-1", email="a@x.com",
        first_name="Abebe", callback_url="https://api/cb")

    assert url == "https://lulit.example/checkout"
    assert captured["url"].endswith("/api/v1/chapa/initiate")
    assert captured["json"]["amount_etb"] == 100
    assert captured["json"]["callback_url"] == "https://api/cb"


@pytest.mark.asyncio
async def test_verify_success_and_failure(monkeypatch):
    monkeypatch.setattr(cs.settings, "chapa_secret_key", "CHASECK_TEST-x")
    _mock_httpx(monkeypatch, get=_Resp(200, {
        "status": "success", "data": {"status": "success", "amount": "100"}}))
    assert (await cs.chapa_service.verify("etg-1"))["amount"] == "100"

    _mock_httpx(monkeypatch, get=_Resp(200, {
        "status": "success", "data": {"status": "pending"}}))
    assert await cs.chapa_service.verify("etg-1") is None   # not yet paid


# ── recharge endpoint + webhook ──────────────────────────────────────────────

async def _setup(db, owner_id):
    db.add(User(id=owner_id, email="owner@x.com", full_name="Owner"))
    biz = Business(id=uuid.uuid4(), owner_id=owner_id, name="Shop", slug=f"s-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    pkg = EtgPackage(id=uuid.uuid4(), name="Starter", etg_amount=5000, bonus_etg=500,
                     price_usd=5.0, price_etb=300.0, is_active=True)
    db.add(pkg)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=0))
    await db.flush()
    return biz, pkg


@pytest.mark.asyncio
async def test_initiate_recharge_returns_payment_url(client, db, sample_user_id, valid_access_token, monkeypatch):
    """Recharge now initiates through the Lulit gateway — mock its HTTP call."""
    biz, pkg = await _setup(db, sample_user_id)
    seen = {}

    monkeypatch.setattr(billing.settings, "lulit_internal_url", "https://lulit.test")

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **k):
            seen["url"] = url
            seen.update(json or {})
            return _Resp(200, {"checkout_url": "https://checkout.chapa.co/pay"})
    monkeypatch.setattr(billing.httpx, "AsyncClient", _Client)

    r = await client.post(f"/api/v1/billing/recharge/{biz.id}",
                          json={"package_id": str(pkg.id), "payment_provider": "chapa"},
                          headers={"Authorization": f"Bearer {valid_access_token}"})
    assert r.status_code == 201, r.text
    assert r.json()["payment_url"] == "https://checkout.chapa.co/pay"
    assert seen["url"] == "https://lulit.test/api/v1/chapa/initiate"
    assert seen["callback_url"].endswith("/billing/webhook/chapa")
    assert seen["meta"] == {"platform": "ethiogram", "business_id": str(biz.id)}
    order = (await db.execute(select(RechargeOrder))).scalars().one()
    assert order.payment_reference == f"etg-{order.id}"   # tx_ref set for the webhook


@pytest.mark.asyncio
async def test_webhook_rejects_missing_or_invalid_signature(client, db, sample_user_id, monkeypatch):
    biz, pkg = await _setup(db, sample_user_id)
    order = RechargeOrder(
        id=uuid.uuid4(), business_id=biz.id, etg_package_id=pkg.id,
        etg_amount=5000, bonus_etg=500, fiat_amount=300.0, fiat_currency="ETB",
        payment_provider=__import__("app.db.models", fromlist=["PaymentProvider"]).PaymentProvider.chapa,
        payment_reference="etg-abc", status=PaymentStatus.pending)
    db.add(order)
    await db.flush()

    monkeypatch.setattr(billing.settings, "chapa_webhook_secret", "super-secret")
    body = b'{"tx_ref": "etg-abc", "status": "success"}'

    missing = await client.post(
        "/api/v1/billing/webhook/chapa",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert missing.status_code == 401

    bad_sig = hmac.new(b"super-secret", body, hashlib.sha256).hexdigest()
    invalid = await client.post(
        "/api/v1/billing/webhook/chapa",
        content=body,
        headers={"content-type": "application/json", "X-Chapa-Signature": bad_sig[:-1]},
    )
    assert invalid.status_code == 401


@pytest.mark.asyncio
async def test_webhook_credits_only_when_verified(client, db, sample_user_id, monkeypatch):
    biz, pkg = await _setup(db, sample_user_id)
    order = RechargeOrder(
        id=uuid.uuid4(), business_id=biz.id, etg_package_id=pkg.id,
        etg_amount=5000, bonus_etg=500, fiat_amount=300.0, fiat_currency="ETB",
        payment_provider=__import__("app.db.models", fromlist=["PaymentProvider"]).PaymentProvider.chapa,
        payment_reference="etg-abc", status=PaymentStatus.pending)
    db.add(order)
    await db.flush()

    from app.services import chapa_service as svc

    # 1. Unverified → no credit
    async def _verify_fail(tx): return None
    monkeypatch.setattr(svc.chapa_service, "verify", _verify_fail)
    await client.post("/api/v1/billing/webhook/chapa", json={"tx_ref": "etg-abc", "status": "success"})
    assert (await db.execute(select(TokenWallet).where(TokenWallet.business_id == biz.id))).scalar_one().balance == 0

    # 2. Verified → wallet credited with etg + bonus
    async def _verify_ok(tx): return {"status": "success", "amount": "300"}
    monkeypatch.setattr(svc.chapa_service, "verify", _verify_ok)
    await client.post("/api/v1/billing/webhook/chapa", json={"tx_ref": "etg-abc", "status": "success"})
    wallet = (await db.execute(select(TokenWallet).where(TokenWallet.business_id == biz.id))).scalar_one()
    assert wallet.balance == 5500 and wallet.lifetime_recharged == 5500
    assert (await db.execute(select(RechargeOrder).where(RechargeOrder.id == order.id))).scalar_one().status == PaymentStatus.completed

    # 3. Idempotent — a second webhook does not double-credit
    await client.post("/api/v1/billing/webhook/chapa", json={"tx_ref": "etg-abc", "status": "success"})
    assert (await db.execute(select(TokenWallet).where(TokenWallet.business_id == biz.id))).scalar_one().balance == 5500
