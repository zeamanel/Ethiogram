"""Native booking engine: pure slot maths + DB-backed availability/persistence."""
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.models import Booking, Business, User
import app.services.booking_service as bs

UTC = timezone.utc


# ── pure slot computation ────────────────────────────────────────────────────

def test_free_slots_full_day_no_busy():
    slots = bs.compute_free_slots(
        date(2030, 1, 7), busy=[], tz="UTC",
        open_hour=9, close_hour=12, duration_minutes=60, num_slots=10)
    assert [s["label"][-8:] for s in slots] == ["09:00 AM", "10:00 AM", "11:00 AM"]
    # slots carry tz-aware ISO start/end
    assert slots[0]["start"] == "2030-01-07T09:00:00+00:00"
    assert slots[0]["end"] == "2030-01-07T10:00:00+00:00"


def test_free_slots_excludes_busy_overlap():
    busy = [(datetime(2030, 1, 7, 10, 0, tzinfo=UTC), datetime(2030, 1, 7, 11, 0, tzinfo=UTC))]
    slots = bs.compute_free_slots(
        date(2030, 1, 7), busy=busy, tz="UTC",
        open_hour=9, close_hour=12, duration_minutes=60, num_slots=10)
    labels = [s["label"][-8:] for s in slots]
    assert labels == ["09:00 AM", "11:00 AM"]   # 10:00 slot removed


def test_free_slots_excludes_past():
    now = datetime(2030, 1, 7, 10, 30, tzinfo=UTC)
    slots = bs.compute_free_slots(
        date(2030, 1, 7), busy=[], tz="UTC",
        open_hour=9, close_hour=12, duration_minutes=60, num_slots=10, now=now)
    # 09:00 and 10:00 are in the past; only 11:00 remains
    assert [s["label"][-8:] for s in slots] == ["11:00 AM"]


def test_free_slots_naive_busy_treated_as_utc():
    # SQLite returns naive datetimes; the engine must not crash comparing them.
    busy = [(datetime(2030, 1, 7, 9, 0), datetime(2030, 1, 7, 10, 0))]
    slots = bs.compute_free_slots(
        date(2030, 1, 7), busy=busy, tz="UTC",
        open_hour=9, close_hour=11, duration_minutes=60)
    assert [s["label"][-8:] for s in slots] == ["10:00 AM"]


def test_free_slots_respects_num_slots_cap():
    slots = bs.compute_free_slots(
        date(2030, 1, 7), busy=[], tz="UTC",
        open_hour=9, close_hour=17, duration_minutes=60, num_slots=3)
    assert len(slots) == 3


@pytest.mark.parametrize("text, mins", [
    ("30 min", 30), ("45 minutes", 45), ("1 hour", 60), ("2 hours", 120),
    ("1.5 hours", 90), ("90", 90), ("1h30m", 90), ("", 60), (None, 60),
    (45, 45), ("nonsense", 60),
])
def test_parse_duration_minutes(text, mins):
    assert bs.parse_duration_minutes(text) == mins


def test_booking_config_defaults_and_overrides():
    assert bs.booking_config(None)["open_hour"] == 9
    cfg = bs.booking_config({"timezone": "UTC", "open_hour": 8, "close_hour": 20,
                             "appointment_duration_minutes": 30})
    assert cfg == {"tz": "UTC", "open_hour": 8, "close_hour": 20, "duration": 30}
    # bad close_hour is clamped above open_hour
    assert bs.booking_config({"open_hour": 10, "close_hour": 3})["close_hour"] == 11


# ── DB-backed availability + persistence ─────────────────────────────────────

async def _biz(db):
    owner = User(id=uuid.uuid4(), telegram_id=5151, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="Salon", slug=f"s-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_create_booking_persists(db):
    biz = await _biz(db)
    start = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)
    booking, created = await bs.create_booking(
        db, business_id=biz.id, customer_platform_id="999",
        starts_at=start, ends_at=start + timedelta(hours=1),
        customer_name="Sara", service_name="Haircut", price="300 ETB")
    assert created is True
    row = (await db.execute(select(Booking).where(Booking.business_id == biz.id))).scalars().one()
    assert row.customer_name == "Sara" and row.service_name == "Haircut"
    assert row.status == "confirmed" and row.price == "300 ETB"


@pytest.mark.asyncio
async def test_create_booking_rejects_overlap(db):
    biz = await _biz(db)
    start = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)
    _, created1 = await bs.create_booking(
        db, business_id=biz.id, customer_platform_id="1",
        starts_at=start, ends_at=start + timedelta(hours=1))
    # overlapping window for the same business → rejected
    booking2, created2 = await bs.create_booking(
        db, business_id=biz.id, customer_platform_id="2",
        starts_at=start + timedelta(minutes=30), ends_at=start + timedelta(minutes=90))
    assert created1 is True
    assert created2 is False and booking2 is None


@pytest.mark.asyncio
async def test_load_bookable_services_from_catalog(db):
    from app.db.models import KnowledgeItem, KnowledgeItemType
    biz = await _biz(db)
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.service,
                         title="Haircut", data={"price": "300 ETB", "duration": "30 min"},
                         is_active=True))
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.service,
                         title="Colour", data={"price": "900 ETB", "duration": "2 hours"},
                         is_active=True))
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.product,
                         title="Shampoo", data={"price": "120 ETB"}, is_active=True))
    await db.flush()

    svcs = await bs.load_bookable_services(db, biz.id)
    assert [s["name"] for s in svcs] == ["Haircut", "Colour"]   # products excluded
    assert svcs[0]["duration_min"] == 30 and svcs[1]["duration_min"] == 120
    assert svcs[0]["price"] == "300 ETB"


@pytest.mark.asyncio
async def test_available_slots_uses_service_duration(db):
    biz = await _biz(db)
    # 9-12 with a 90-min service → 09:00 and 10:30 fit (12:00 end excluded)
    slots = await bs.available_slots(
        db, biz.id, {"timezone": "UTC", "open_hour": 9, "close_hour": 12},
        date(2030, 4, 1), num_slots=10, duration_minutes=90)
    assert [s["label"][-8:] for s in slots] == ["09:00 AM", "10:30 AM"]


@pytest.mark.asyncio
async def test_available_slots_excludes_booked(db):
    biz = await _biz(db)
    start = datetime(2030, 3, 1, 10, 0, tzinfo=UTC)
    await bs.create_booking(
        db, business_id=biz.id, customer_platform_id="1",
        starts_at=start, ends_at=start + timedelta(hours=1))
    slots = await bs.available_slots(
        db, biz.id, {"timezone": "UTC", "open_hour": 9, "close_hour": 12,
                     "appointment_duration_minutes": 60},
        date(2030, 3, 1), num_slots=10)
    labels = [s["label"][-8:] for s in slots]
    assert "10:00 AM" not in labels       # the booked hour is gone
    assert "09:00 AM" in labels and "11:00 AM" in labels
