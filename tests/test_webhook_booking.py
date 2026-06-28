"""Webhook booking-callback dispatch tests.

Proves the end-to-end callback path with the NATIVE booking engine: a tapped
slot is persisted as a Booking in our own DB (the source of truth), the customer
is told it's confirmed, Google Calendar is only a best-effort mirror, and a slot
that was taken in the meantime is rejected instead of double-booked.
"""
import json
import uuid

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from app.agents.concierge import ConciergeAgent
from app.api import webhooks
from app.core.security import encrypt_child_secrets
from app.db.models import Agent, AgentStatus, Booking, ChildAgent

_FAKE_CREDS = '{"type":"service_account","project_id":"x","private_key":"-----BEGIN PRIVATE KEY-----\\nMIIB\\n-----END PRIVATE KEY-----\\n","client_email":"x@x.iam.gserviceaccount.com","token_uri":"https://oauth2.googleapis.com/token"}'

_SLOT = {"start": "2026-06-22T09:00:00", "end": "2026-06-22T10:00:00", "label": "Mon 09:00 AM"}


class _Bot:
    def __init__(self):
        self.id = uuid.uuid4()


class _Conv:
    def __init__(self, business_id):
        self.business_id = business_id
        self.id = uuid.uuid4()


class _Env:
    customer_id = "959519454"
    customer_name = "Abebe"


async def _deploy_concierge(db, business_id, *, with_calendar=True):
    from app.core.security import encrypt_agent_prompt
    enc, key_ref = encrypt_agent_prompt("You are a concierge.", str(uuid.uuid4()))
    father = Agent(
        creator_id=uuid.uuid4(), name="Booker", tagline="t", description="d",
        category="concierge", tags=[], capabilities=[],
        encrypted_system_prompt=enc, encryption_key_ref=key_ref,
        price_etg=100, status=AgentStatus.live,
    )
    db.add(father)
    await db.flush()
    secrets = encrypt_child_secrets(
        {"calendar_id": "cal@example.com", "credentials_json": _FAKE_CREDS}
    ) if with_calendar else None
    child = ChildAgent(
        agent_id=father.id, business_id=business_id, is_active=True,
        child_data={"services": "Consultation"},
        child_secrets=secrets,
    )
    db.add(child)
    await db.flush()


@pytest.fixture
def captured(monkeypatch):
    sent: list[str] = []

    async def _send_message(token, chat_id, text, **kw):
        sent.append(text)
        return {}

    async def _answer(token, cb_id, **kw):
        return True

    monkeypatch.setattr(webhooks.telegram_service, "send_message", _send_message)
    monkeypatch.setattr(webhooks.telegram_service, "answer_callback_query", _answer)
    return sent


@pytest.mark.asyncio
async def test_callback_persists_booking_even_if_calendar_mirror_fails(db, captured, monkeypatch):
    """The whole point of the native engine: a calendar misconfiguration must
    NOT block the booking. We own the record; the customer is confirmed."""
    biz = uuid.uuid4()
    await _deploy_concierge(db, biz)        # has calendar creds...
    bot, conv = _Bot(), _Conv(biz)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    key = "abcdef123456"
    await redis.set(f"book:{bot.id}:{key}", json.dumps([_SLOT]))

    # ...but the Google Calendar insert blows up
    def _boom(*a, **k):
        raise RuntimeError("Calendar API 500")
    monkeypatch.setattr(ConciergeAgent, "_create_event_sync", _boom)

    cb = {"id": "cb1", "data": f"book_slot:{key}:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)

    assert "confirmed" in captured[-1].lower() and "✅" in captured[-1]
    # the Booking is persisted in OUR db, with no calendar mirror id
    row = (await db.execute(select(Booking).where(Booking.business_id == biz))).scalars().one()
    assert row.customer_name == "Abebe" and row.service_name == "Consultation"
    assert row.calendar_event_id is None
    # slot consumed so a re-tap can't double-book
    assert await redis.get(f"book:{bot.id}:{key}") is None


@pytest.mark.asyncio
async def test_callback_no_calendar_still_books(db, captured):
    """Credential-free path: a Concierge with NO calendar still takes bookings."""
    biz = uuid.uuid4()
    await _deploy_concierge(db, biz, with_calendar=False)
    bot, conv = _Bot(), _Conv(biz)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    key = "0a0a0a0a0a0a"
    await redis.set(f"book:{bot.id}:{key}", json.dumps([_SLOT]))

    cb = {"id": "cbn", "data": f"book_slot:{key}:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)

    assert "confirmed" in captured[-1].lower()
    assert (await db.execute(select(Booking).where(Booking.business_id == biz))).scalars().one()


@pytest.mark.asyncio
async def test_callback_success_mirrors_calendar_id(db, captured, monkeypatch):
    biz = uuid.uuid4()
    await _deploy_concierge(db, biz)
    bot, conv = _Bot(), _Conv(biz)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    key = "feedface0001"
    await redis.set(f"book:{bot.id}:{key}", json.dumps([_SLOT]))

    def _ok(*a, **k):
        return {"id": "evt_999", "status": "confirmed"}
    monkeypatch.setattr(ConciergeAgent, "_create_event_sync", _ok)

    cb = {"id": "cb2", "data": f"book_slot:{key}:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)

    assert "confirmed" in captured[-1].lower()
    row = (await db.execute(select(Booking).where(Booking.business_id == biz))).scalars().one()
    assert row.calendar_event_id == "evt_999"     # mirrored
    assert await redis.get(f"book:{bot.id}:{key}") is None


@pytest.mark.asyncio
async def test_callback_double_booking_is_rejected(db, captured):
    """A slot taken between offer and tap is rejected, not double-booked."""
    biz = uuid.uuid4()
    await _deploy_concierge(db, biz, with_calendar=False)
    bot, conv = _Bot(), _Conv(biz)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    key = "beadbeadbead"
    await redis.set(f"book:{bot.id}:{key}", json.dumps([_SLOT]))

    cb = {"id": "cbA", "data": f"book_slot:{key}:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)
    assert "confirmed" in captured[-1].lower()

    # second customer taps the same (now-taken) slot
    await redis.set(f"book:{bot.id}:{key}", json.dumps([_SLOT]))
    cb2 = {"id": "cbB", "data": f"book_slot:{key}:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb2)
    assert "just taken" in captured[-1].lower()
    # only ONE booking exists for that slot
    rows = (await db.execute(select(Booking).where(Booking.business_id == biz))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_callback_expired_session_is_handled(db, captured):
    bot, conv = _Bot(), _Conv(uuid.uuid4())
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cb = {"id": "cb3", "data": "book_slot:missingkey:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)
    assert "expired" in captured[-1].lower()
