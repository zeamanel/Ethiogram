"""Tests for the internal service-to-service credit endpoint (called by Odaflux/Lulit)."""
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Business, TokenWallet, User


def _payload(business_id, amount=100, **over):
    body = {"user_id": str(uuid.uuid4()), "business_id": str(business_id),
            "amount": amount, "tx_ref": "etg-test-001"}
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_unconfigured_key_returns_500(client):
    # settings.chapa_internal_key defaults to "" — endpoint must refuse to run.
    resp = await client.post("/api/v1/internal/chapa-credit", json=_payload(uuid.uuid4()))
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_wrong_key_returns_401(client, monkeypatch):
    import app.api.internal as m
    monkeypatch.setattr(m.settings, "chapa_internal_key", "secret-abc")
    resp = await client.post("/api/v1/internal/chapa-credit",
                             headers={"X-Internal-Key": "wrong"},
                             json=_payload(uuid.uuid4()))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_credit_applied_to_wallet(client, db, monkeypatch):
    import app.api.internal as m
    monkeypatch.setattr(m.settings, "chapa_internal_key", "secret-abc")

    owner_id = uuid.uuid4()
    db.add(User(id=owner_id, email="int@x.com", full_name="Int"))
    biz = Business(id=uuid.uuid4(), owner_id=owner_id, name="IntBiz",
                   slug=f"int-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=500))
    await db.flush()

    resp = await client.post(
        "/api/v1/internal/chapa-credit",
        headers={"X-Internal-Key": "secret-abc"},
        json=_payload(biz.id, amount=1000),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["new_balance"] == 1500

    # Re-query: metering_service.credit() fetches its own wallet object so the
    # in-memory reference held by the test won't auto-update.
    fresh = (await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == biz.id)
    )).scalar_one()
    assert fresh.balance == 1500
    assert fresh.lifetime_recharged == 1000


@pytest.mark.asyncio
async def test_bad_amount_rejected(client, db, monkeypatch):
    import app.api.internal as m
    monkeypatch.setattr(m.settings, "chapa_internal_key", "s")

    owner_id = uuid.uuid4()
    db.add(User(id=owner_id, email="bad@x.com", full_name="Bad"))
    biz = Business(id=uuid.uuid4(), owner_id=owner_id, name="B", slug=f"b-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=0))
    await db.flush()

    hdr = {"X-Internal-Key": "s"}
    url = "/api/v1/internal/chapa-credit"
    # zero / negative → 400 from the endpoint's guard
    for bad in (0, -1):
        r = await client.post(url, headers=hdr, json=_payload(biz.id, amount=bad))
        assert r.status_code == 400, f"expected 400 for amount={bad!r}"
    # non-integer → 422 from pydantic validation
    for bad in ("abc", None):
        r = await client.post(url, headers=hdr, json=_payload(biz.id, amount=bad))
        assert r.status_code == 422, f"expected 422 for amount={bad!r}"
