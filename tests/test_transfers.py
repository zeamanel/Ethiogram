"""Business ownership transfer: initiate (owner) → accept/decline (recipient)."""
import uuid

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models import Business, BusinessTransfer, User


def _hdr(user_id, role="owner"):
    return {"Authorization": f"Bearer {create_access_token(user_id, role=role)}"}


async def _owner_with_biz(db, user_id, *, username="alice", email="alice@x.com", tg=111):
    db.add(User(id=user_id, username=username, email=email, telegram_id=tg, role=__import__(
        "app.db.models", fromlist=["UserRole"]).UserRole.owner, is_active=True))
    biz = Business(id=uuid.uuid4(), owner_id=user_id, name="Selam Store", slug=f"s-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    return biz


# ── initiate (owner) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_initiate_transfer_to_email(client, db, sample_user_id, valid_access_token):
    biz = await _owner_with_biz(db, sample_user_id)
    r = await client.post(f"/api/v1/transfers/business/{biz.id}",
                          json={"to_kind": "email", "to_value": "BOB@x.com"},
                          headers={"Authorization": f"Bearer {valid_access_token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["to_value"] == "bob@x.com"   # normalized
    assert body["recipient_has_account"] is False                            # bob doesn't exist yet
    row = (await db.execute(select(BusinessTransfer))).scalars().one()
    assert row.status == "pending" and row.to_kind == "email"


@pytest.mark.asyncio
async def test_cannot_transfer_to_self(client, db, sample_user_id, valid_access_token):
    biz = await _owner_with_biz(db, sample_user_id, username="alice")
    r = await client.post(f"/api/v1/transfers/business/{biz.id}",
                          json={"to_kind": "telegram_username", "to_value": "@alice"},
                          headers={"Authorization": f"Bearer {valid_access_token}"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_initiate_replaces_previous_pending(client, db, sample_user_id, valid_access_token):
    biz = await _owner_with_biz(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.post(f"/api/v1/transfers/business/{biz.id}",
                      json={"to_kind": "email", "to_value": "one@x.com"}, headers=hdr)
    await client.post(f"/api/v1/transfers/business/{biz.id}",
                      json={"to_kind": "email", "to_value": "two@x.com"}, headers=hdr)
    rows = (await db.execute(select(BusinessTransfer).where(
        BusinessTransfer.status == "pending"))).scalars().all()
    assert len(rows) == 1 and rows[0].to_value == "two@x.com"


@pytest.mark.asyncio
async def test_owner_can_cancel(client, db, sample_user_id, valid_access_token):
    biz = await _owner_with_biz(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.post(f"/api/v1/transfers/business/{biz.id}",
                      json={"to_kind": "email", "to_value": "bob@x.com"}, headers=hdr)
    assert (await client.delete(f"/api/v1/transfers/business/{biz.id}", headers=hdr)).status_code == 204
    assert (await client.get(f"/api/v1/transfers/business/{biz.id}", headers=hdr)).json()["status"] == "none"


@pytest.mark.asyncio
async def test_initiate_requires_ownership(client, db, sample_user_id, valid_access_token):
    other = uuid.uuid4()
    db.add(User(id=other))
    biz = Business(id=uuid.uuid4(), owner_id=other, name="NotMine", slug=f"n-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    db.add(User(id=sample_user_id))
    await db.flush()
    r = await client.post(f"/api/v1/transfers/business/{biz.id}",
                          json={"to_kind": "email", "to_value": "bob@x.com"},
                          headers={"Authorization": f"Bearer {valid_access_token}"})
    assert r.status_code == 404


# ── accept / decline (recipient) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recipient_sees_and_accepts(client, db, sample_user_id, valid_access_token):
    biz = await _owner_with_biz(db, sample_user_id, email="alice@x.com")
    # recipient (the logged-in user is alice; transfer to bob). Make a bob user + token.
    bob = uuid.uuid4()
    db.add(User(id=bob, email="bob@x.com", telegram_id=222, is_active=True))
    await db.flush()
    # alice initiates to bob's email
    await client.post(f"/api/v1/transfers/business/{biz.id}",
                      json={"to_kind": "email", "to_value": "bob@x.com"},
                      headers={"Authorization": f"Bearer {valid_access_token}"})

    bob_hdr = _hdr(bob)
    incoming = (await client.get("/api/v1/transfers/incoming", headers=bob_hdr)).json()
    assert len(incoming) == 1 and incoming[0]["business_name"] == "Selam Store"
    tid = incoming[0]["id"]

    acc = await client.post(f"/api/v1/transfers/incoming/{tid}/accept", headers=bob_hdr)
    assert acc.status_code == 200, acc.text
    # ownership moved to bob
    row = (await db.execute(select(Business).where(Business.id == biz.id))).scalar_one()
    assert row.owner_id == bob
    # alice no longer sees it as hers
    assert (await client.get(f"/api/v1/transfers/business/{biz.id}",
                             headers={"Authorization": f"Bearer {valid_access_token}"})).status_code == 404


@pytest.mark.asyncio
async def test_non_recipient_cannot_accept(client, db, sample_user_id, valid_access_token):
    biz = await _owner_with_biz(db, sample_user_id)
    await client.post(f"/api/v1/transfers/business/{biz.id}",
                      json={"to_kind": "email", "to_value": "bob@x.com"},
                      headers={"Authorization": f"Bearer {valid_access_token}"})
    t = (await db.execute(select(BusinessTransfer))).scalars().one()
    # a stranger (carol) tries to accept bob's transfer
    carol = uuid.uuid4()
    db.add(User(id=carol, email="carol@x.com", is_active=True))
    await db.flush()
    r = await client.post(f"/api/v1/transfers/incoming/{t.id}/accept", headers=_hdr(carol))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_recipient_can_decline(client, db, sample_user_id, valid_access_token):
    biz = await _owner_with_biz(db, sample_user_id)
    bob = uuid.uuid4()
    db.add(User(id=bob, telegram_id=222, is_active=True))
    await db.flush()
    await client.post(f"/api/v1/transfers/business/{biz.id}",
                      json={"to_kind": "telegram_id", "to_value": "222"},
                      headers={"Authorization": f"Bearer {valid_access_token}"})
    t = (await db.execute(select(BusinessTransfer))).scalars().one()
    assert (await client.post(f"/api/v1/transfers/incoming/{t.id}/decline",
                              headers=_hdr(bob))).status_code == 204
    row = (await db.execute(select(Business).where(Business.id == biz.id))).scalar_one()
    assert row.owner_id == sample_user_id           # unchanged
