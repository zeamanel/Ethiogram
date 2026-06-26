"""Tests for the Mini App admin dashboard API (JWT admin gate, no secret header)."""
import uuid

import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models import (
    Bot,
    BotStatus,
    Business,
    ChildAgent,
    TokenWallet,
    UsageEvent,
    User,
    UserRole,
)


@pytest.fixture
async def admin(db):
    u = User(id=uuid.uuid4(), email="admin@ethiogram.com", telegram_id=959519454,
             is_admin=True, role=UserRole.admin, is_active=True)
    db.add(u)
    await db.flush()
    return u


@pytest.fixture
def admin_hdr(admin):
    return {"Authorization": f"Bearer {create_access_token(admin.id, role='admin')}"}


async def _business(db, *, name="Acme", slug=None, suspended=False, owner_email=None):
    owner = User(id=uuid.uuid4(), email=owner_email or f"o-{uuid.uuid4().hex[:8]}@x.com",
                 role=UserRole.owner, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name=name,
                   slug=slug or f"b-{uuid.uuid4().hex[:8]}", is_suspended=suspended)
    db.add(biz)
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_non_admin_is_rejected(client, db, sample_user_id, valid_access_token):
    db.add(User(id=sample_user_id, role=UserRole.owner, is_active=True))
    await db.flush()
    resp = await client.get("/api/v1/admin/stats",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_stats(client, db, admin, admin_hdr):
    biz = await _business(db, name="Live Co")
    await _business(db, name="Suspended Co", suspended=True)
    db.add(Bot(id=uuid.uuid4(), business_id=biz.id, encrypted_token="x",
               token_hash=uuid.uuid4().hex, status=BotStatus.active))
    db.add(UsageEvent(id=uuid.uuid4(), business_id=biz.id, action_type="ai_reply", etg_charged=42))
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=100, lifetime_recharged=5000))
    await db.flush()

    body = (await client.get("/api/v1/admin/stats", headers=admin_hdr)).json()
    assert body["total_businesses"] == 2
    assert body["suspended_businesses"] == 1
    assert body["active_businesses"] == 1
    assert body["total_bots"] == 1
    assert body["total_etg_spent"] == 42
    assert body["total_revenue_etg"] == 5000
    assert body["total_users"] >= 2          # the two owners (admin excluded)


@pytest.mark.asyncio
async def test_list_businesses_search_and_filter(client, db, admin, admin_hdr):
    await _business(db, name="Selam Store", owner_email="selam@x.com")
    await _business(db, name="Other Shop", suspended=True, owner_email="other@x.com")

    all_r = (await client.get("/api/v1/admin/businesses", headers=admin_hdr)).json()
    assert all_r["total"] == 2 and len(all_r["items"]) == 2
    assert {"owner_email", "bot_count", "agent_count", "is_suspended"} <= set(all_r["items"][0])

    susp = (await client.get("/api/v1/admin/businesses?status=suspended", headers=admin_hdr)).json()
    assert susp["total"] == 1 and susp["items"][0]["name"] == "Other Shop"

    found = (await client.get("/api/v1/admin/businesses?search=selam", headers=admin_hdr)).json()
    assert found["total"] == 1 and found["items"][0]["name"] == "Selam Store"


@pytest.mark.asyncio
async def test_suspend_and_unsuspend(client, db, admin, admin_hdr):
    biz = await _business(db, name="Toggle Co")
    r1 = await client.patch(f"/api/v1/admin/businesses/{biz.id}/suspend",
                            json={"suspend": True, "reason": "spam"}, headers=admin_hdr)
    assert r1.status_code == 200 and r1.json()["is_suspended"] is True
    await db.refresh(biz)
    assert biz.is_suspended is True and biz.suspended_reason == "spam"

    r2 = await client.patch(f"/api/v1/admin/businesses/{biz.id}/suspend",
                            json={"suspend": False}, headers=admin_hdr)
    assert r2.json()["is_suspended"] is False


@pytest.mark.asyncio
async def test_delete_business_and_logs(client, db, admin, admin_hdr):
    # NOTE: child cascade is enforced at the DB level (FK ondelete=CASCADE) in
    # prod/Postgres; SQLite doesn't run FK cascades, so we assert the endpoint
    # removes the business and writes the audit log.
    from app.db.models import AdminAuditLog
    biz = await _business(db, name="Doomed Co")

    resp = await client.delete(f"/api/v1/admin/businesses/{biz.id}", headers=admin_hdr)
    assert resp.status_code == 204
    assert (await db.execute(select(Business).where(Business.id == biz.id))).scalar_one_or_none() is None
    logged = (await db.execute(select(AdminAuditLog).where(
        AdminAuditLog.action == "business.delete"))).scalars().all()
    assert any(l.target_id == str(biz.id) for l in logged)


@pytest.mark.asyncio
async def test_list_users_and_toggle_admin(client, db, admin, admin_hdr):
    target = User(id=uuid.uuid4(), email="promote@x.com", role=UserRole.owner, is_active=True)
    db.add(target)
    await db.flush()

    listing = (await client.get("/api/v1/admin/users?search=promote", headers=admin_hdr)).json()
    assert listing["total"] == 1 and listing["items"][0]["is_admin"] is False

    grant = await client.patch(f"/api/v1/admin/users/{target.id}/admin",
                               json={"is_admin": True}, headers=admin_hdr)
    assert grant.status_code == 200 and grant.json()["is_admin"] is True
    # endpoint mutates the same in-session object — read directly (refresh would
    # revert the not-yet-committed change)
    assert target.is_admin is True and target.role == UserRole.admin


@pytest.mark.asyncio
async def test_cannot_revoke_own_admin(client, db, admin, admin_hdr):
    resp = await client.patch(f"/api/v1/admin/users/{admin.id}/admin",
                              json={"is_admin": False}, headers=admin_hdr)
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_system_status(client, db, admin, admin_hdr, mock_redis):
    body = (await client.get("/api/v1/admin/system/status", headers=admin_hdr)).json()
    assert body["database"]["status"] == "ok"
    assert body["redis"]["status"] in ("ok", "degraded")
    assert "embedding_backlog" in body["workers"]
