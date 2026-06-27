"""Tests for business billing settings: GET/PATCH /businesses/{id}/billing and
GET /businesses/{id}/users/usage."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import Bot, BotStatus, Business, Conversation, User, UserRole


async def _seed(db, user_id, business_id, slug="acme"):
    db.add(User(id=user_id, role=UserRole.owner, is_active=True))
    db.add(Business(id=business_id, owner_id=user_id, name="Acme", slug=slug))
    await db.flush()


@pytest.mark.asyncio
async def test_billing_defaults_are_backward_compatible(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    body = (await client.get(f"/api/v1/businesses/{sample_business_id}/billing",
                             headers={"Authorization": f"Bearer {valid_access_token}"})).json()
    assert body["billing_policy"] == "business_pays"
    assert body["per_user_monthly_limit"] is None
    assert body["per_user_limit_action"] == "block"
    assert body["service_price"] == 0 and body["business_markup"] == 0


@pytest.mark.asyncio
async def test_patch_billing_persists(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    resp = await client.patch(
        f"/api/v1/businesses/{sample_business_id}/billing",
        json={"billing_policy": "user_pays", "per_user_monthly_limit": 100,
              "per_user_limit_action": "user_pays", "service_price": 5, "business_markup": 2},
        headers=hdr,
    )
    assert resp.status_code == 200, resp.text
    b = resp.json()
    assert b["billing_policy"] == "user_pays" and b["service_price"] == 5 and b["business_markup"] == 2

    biz = (await db.execute(select(Business).where(Business.id == sample_business_id))).scalar_one()
    assert biz.billing_policy == "user_pays" and biz.per_user_monthly_limit == 100
    assert biz.per_user_limit_action == "user_pays"

    # clearing the limit (explicit null)
    cleared = await client.patch(f"/api/v1/businesses/{sample_business_id}/billing",
                                 json={"per_user_monthly_limit": None}, headers=hdr)
    assert cleared.json()["per_user_monthly_limit"] is None


@pytest.mark.asyncio
async def test_billing_validation(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    assert (await client.patch(f"/api/v1/businesses/{sample_business_id}/billing",
                               json={"billing_policy": "free_lunch"}, headers=hdr)).status_code == 422
    assert (await client.patch(f"/api/v1/businesses/{sample_business_id}/billing",
                               json={"per_user_limit_action": "explode"}, headers=hdr)).status_code == 422
    assert (await client.patch(f"/api/v1/businesses/{sample_business_id}/billing",
                               json={"service_price": -1}, headers=hdr)).status_code == 422


@pytest.mark.asyncio
async def test_user_usage_list(client, db, sample_user_id, sample_business_id, valid_access_token):
    await _seed(db, sample_user_id, sample_business_id)
    bot = Bot(id=uuid.uuid4(), business_id=sample_business_id, encrypted_token="x",
              token_hash=uuid.uuid4().hex, status=BotStatus.active)
    db.add(bot)
    await db.flush()
    for name, used, bal in [("Abebe", 80, 0), ("Selam", 200, 50), ("Kebede", 10, 0)]:
        db.add(Conversation(id=uuid.uuid4(), business_id=sample_business_id, bot_id=bot.id,
                            customer_platform_id=f"tg-{name}", customer_name=name,
                            monthly_etg_used=used, etg_balance=bal,
                            last_message_at=datetime.now(timezone.utc)))
    await db.flush()
    hdr = {"Authorization": f"Bearer {valid_access_token}"}

    res = (await client.get(f"/api/v1/businesses/{sample_business_id}/users/usage", headers=hdr)).json()
    assert res["total"] == 3
    # ordered by monthly usage desc
    assert [r["customer_name"] for r in res["items"]] == ["Selam", "Abebe", "Kebede"]
    assert res["items"][0]["monthly_etg_used"] == 200 and res["items"][0]["etg_balance"] == 50

    found = (await client.get(f"/api/v1/businesses/{sample_business_id}/users/usage?search=Abebe",
                              headers=hdr)).json()
    assert found["total"] == 1 and found["items"][0]["customer_name"] == "Abebe"


@pytest.mark.asyncio
async def test_billing_requires_ownership(client, db, sample_user_id, valid_access_token):
    other = uuid.uuid4()
    db.add(User(id=sample_user_id, role=UserRole.owner, is_active=True))
    db.add(Business(id=other, owner_id=uuid.uuid4(), name="Theirs", slug="theirs-bill"))
    await db.flush()
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    assert (await client.get(f"/api/v1/businesses/{other}/billing", headers=hdr)).status_code == 404
    assert (await client.patch(f"/api/v1/businesses/{other}/billing",
                               json={"service_price": 5}, headers=hdr)).status_code == 404
