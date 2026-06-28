"""Owner Appointments API: list (upcoming/past) + status update, owner-scoped."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import Booking, Business, User

UTC = timezone.utc


async def _seed(db, owner_id):
    db.add(User(id=owner_id))
    biz = Business(id=uuid.uuid4(), owner_id=owner_id, name="Salon", slug=f"s-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    return biz


def _booking(biz_id, *, hours_from_now, name, status="confirmed"):
    start = datetime.now(UTC) + timedelta(hours=hours_from_now)
    return Booking(id=uuid.uuid4(), business_id=biz_id, customer_platform_id="1",
                   customer_name=name, service_name="Haircut",
                   starts_at=start, ends_at=start + timedelta(hours=1), status=status)


@pytest.mark.asyncio
async def test_list_upcoming_appointments(client, db, sample_user_id, valid_access_token):
    biz = await _seed(db, sample_user_id)
    db.add(_booking(biz.id, hours_from_now=24, name="Future Fae"))
    db.add(_booking(biz.id, hours_from_now=-24, name="Past Pat"))
    await db.flush()

    resp = await client.get(f"/api/v1/dashboard/appointments/{biz.id}",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    names = [a["customer_name"] for a in resp.json()]
    assert names == ["Future Fae"]                 # only upcoming, past excluded
    assert resp.json()[0]["synced_to_calendar"] is False


@pytest.mark.asyncio
async def test_list_past_scope(client, db, sample_user_id, valid_access_token):
    biz = await _seed(db, sample_user_id)
    db.add(_booking(biz.id, hours_from_now=24, name="Future Fae"))
    db.add(_booking(biz.id, hours_from_now=-24, name="Past Pat"))
    await db.flush()

    resp = await client.get(f"/api/v1/dashboard/appointments/{biz.id}?scope=past",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert [a["customer_name"] for a in resp.json()] == ["Past Pat"]


@pytest.mark.asyncio
async def test_cancel_appointment(client, db, sample_user_id, valid_access_token):
    biz = await _seed(db, sample_user_id)
    b = _booking(biz.id, hours_from_now=48, name="Cancel Cara")
    db.add(b)
    await db.flush()

    resp = await client.patch(
        f"/api/v1/dashboard/appointments/{biz.id}/{b.id}",
        json={"status": "cancelled"},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    row = (await db.execute(select(Booking).where(Booking.id == b.id))).scalar_one()
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_invalid_status_rejected(client, db, sample_user_id, valid_access_token):
    biz = await _seed(db, sample_user_id)
    b = _booking(biz.id, hours_from_now=10, name="X")
    db.add(b)
    await db.flush()
    resp = await client.patch(
        f"/api/v1/dashboard/appointments/{biz.id}/{b.id}",
        json={"status": "bogus"},
        headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_appointments_owner_scoped(client, db, sample_user_id, valid_access_token):
    # a business owned by SOMEONE ELSE must not be visible
    other = uuid.uuid4()
    db.add(User(id=other))
    biz = Business(id=uuid.uuid4(), owner_id=other, name="NotYours", slug=f"n-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    db.add(User(id=sample_user_id))
    await db.flush()

    resp = await client.get(f"/api/v1/dashboard/appointments/{biz.id}",
                            headers={"Authorization": f"Bearer {valid_access_token}"})
    assert resp.status_code == 404
