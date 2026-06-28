"""Appointment reminder worker: due-logic + end-to-end scan/send/mark."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import encrypt
from app.db.models import Booking, Bot, BotStatus, Business, Platform, User
import workers.booking_reminders as br

UTC = timezone.utc
NOW = datetime(2030, 5, 1, 12, 0, tzinfo=UTC)


# ── pure due-logic ───────────────────────────────────────────────────────────

def test_due_reminder_1h_window():
    starts = NOW + timedelta(minutes=40)
    assert br.due_reminder(starts, NOW, None, None) == "1h"
    # already sent → nothing
    assert br.due_reminder(starts, NOW, None, NOW) is None


def test_due_reminder_24h_window():
    starts = NOW + timedelta(hours=10)
    assert br.due_reminder(starts, NOW, None, None) == "24h"
    assert br.due_reminder(starts, NOW, NOW, None) is None


def test_due_reminder_far_future_and_past():
    assert br.due_reminder(NOW + timedelta(hours=30), NOW, None, None) is None   # >24h
    assert br.due_reminder(NOW - timedelta(hours=1), NOW, None, None) is None    # past


def test_due_reminder_within_hour_does_not_send_24h():
    # created <1h before start: 1h already sent, must NOT fall back to 24h text
    starts = NOW + timedelta(minutes=20)
    assert br.due_reminder(starts, NOW, None, NOW) is None


def test_reminder_text_variants():
    one = br.reminder_text("1h", service="Haircut", when="Wed 03:00 PM", business="Selam")
    day = br.reminder_text("24h", service="Haircut", when="Wed 03:00 PM", business="Selam")
    assert "about an hour" in one and "Selam" in one
    assert "tomorrow" in day and "reschedule" in day


# ── end-to-end scan ──────────────────────────────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def _send(token, chat_id, text, **kw):
        calls.append({"chat_id": chat_id, "text": text})
    monkeypatch.setattr(br.telegram_service, "send_message", _send)
    return calls


async def _biz_with_bot(db):
    owner = User(id=uuid.uuid4(), telegram_id=700, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="Selam Salon",
                   slug=f"s-{uuid.uuid4().hex[:6]}", timezone="UTC")
    db.add(biz)
    db.add(Bot(id=uuid.uuid4(), business_id=biz.id, platform=Platform.telegram,
               bot_username="b", token_hash="h" + uuid.uuid4().hex[:40],
               encrypted_token=encrypt("CUSTOMER_BOT_TOKEN"), status=BotStatus.active))
    await db.flush()
    return biz


def _booking(biz_id, *, start, status="confirmed", **kw):
    return Booking(id=uuid.uuid4(), business_id=biz_id, customer_platform_id="555",
                   customer_name="Sara", service_name="Haircut",
                   starts_at=start, ends_at=start + timedelta(hours=1),
                   status=status, **kw)


@pytest.mark.asyncio
async def test_scan_sends_and_marks_24h(db, sent):
    biz = await _biz_with_bot(db)
    b = _booking(biz.id, start=NOW + timedelta(hours=10))
    db.add(b)
    await db.flush()

    count = await br.scan_and_send(db, now=NOW)
    assert count == 1
    assert sent[0]["chat_id"] == "555" and "tomorrow" in sent[0]["text"]
    row = (await db.execute(select(Booking).where(Booking.id == b.id))).scalar_one()
    assert row.reminder_24h_sent_at is not None and row.reminder_1h_sent_at is None

    # second run does not re-send the 24h reminder
    assert await br.scan_and_send(db, now=NOW) == 0


@pytest.mark.asyncio
async def test_scan_skips_cancelled_and_past(db, sent):
    biz = await _biz_with_bot(db)
    db.add(_booking(biz.id, start=NOW + timedelta(hours=5), status="cancelled"))
    db.add(_booking(biz.id, start=NOW - timedelta(hours=2)))   # past
    await db.flush()
    assert await br.scan_and_send(db, now=NOW) == 0
    assert sent == []
