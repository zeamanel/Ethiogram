"""Webhook booking-callback dispatch tests.

Proves the end-to-end callback path: a tapped slot is confirmed against the real
calendar, and the message actually SENT to the customer never claims success
when the calendar write fails.
"""
import json
import uuid

import fakeredis.aioredis
import pytest

from app.agents.concierge import ConciergeAgent
from app.api import webhooks
from app.core.security import encrypt_child_secrets
from app.db.models import Agent, AgentStatus, ChildAgent

_FAKE_CREDS = '{"type":"service_account","project_id":"x","private_key":"-----BEGIN PRIVATE KEY-----\\nMIIB\\n-----END PRIVATE KEY-----\\n","client_email":"x@x.iam.gserviceaccount.com","token_uri":"https://oauth2.googleapis.com/token"}'

_SLOT = {"start": "2026-06-22T09:00:00", "end": "2026-06-22T10:00:00", "label": "Mon 09:00 AM"}


class _Bot:
    def __init__(self):
        self.id = uuid.uuid4()


class _Conv:
    def __init__(self, business_id):
        self.business_id = business_id


class _Env:
    customer_id = "959519454"
    customer_name = "Abebe"


async def _deploy_concierge(db, business_id):
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
    child = ChildAgent(
        agent_id=father.id, business_id=business_id, is_active=True,
        child_data={"services": "Consultation"},
        child_secrets=encrypt_child_secrets(
            {"calendar_id": "cal@example.com", "credentials_json": _FAKE_CREDS}
        ),
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
async def test_callback_calendar_failure_tells_customer_it_failed(db, captured, monkeypatch):
    biz = uuid.uuid4()
    await _deploy_concierge(db, biz)
    bot, conv = _Bot(), _Conv(biz)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # stash the offered slot under the session key the callback references
    key = "abcdef123456"
    await redis.set(f"book:{bot.id}:{key}", json.dumps([_SLOT]))

    # force the real calendar insert to fail
    def _boom(*a, **k):
        raise RuntimeError("Calendar API 500")
    monkeypatch.setattr(ConciergeAgent, "_create_event_sync", _boom)

    cb = {"id": "cb1", "data": f"book_slot:{key}:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)

    assert captured, "a message must be sent to the customer"
    msg = captured[-1].lower()
    assert "did not go through" in msg          # explicit failure
    assert "✅" not in captured[-1]
    assert "confirmed" not in msg.replace("couldn't confirm", "")  # never fake success
    # slot session NOT consumed on failure -> customer can retry
    assert await redis.get(f"book:{bot.id}:{key}") is not None


@pytest.mark.asyncio
async def test_callback_success_confirms_and_consumes_slot(db, captured, monkeypatch):
    biz = uuid.uuid4()
    await _deploy_concierge(db, biz)
    bot, conv = _Bot(), _Conv(biz)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    key = "feedface0001"
    await redis.set(f"book:{bot.id}:{key}", json.dumps([_SLOT]))

    # real calendar returns a created event with an id
    def _ok(*a, **k):
        return {"id": "evt_999", "status": "confirmed"}
    monkeypatch.setattr(ConciergeAgent, "_create_event_sync", _ok)

    cb = {"id": "cb2", "data": f"book_slot:{key}:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)

    assert "confirmed" in captured[-1].lower()
    # slot consumed so a re-tap can't double-book
    assert await redis.get(f"book:{bot.id}:{key}") is None


@pytest.mark.asyncio
async def test_callback_expired_session_is_handled(db, captured):
    bot, conv = _Bot(), _Conv(uuid.uuid4())
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cb = {"id": "cb3", "data": "book_slot:missingkey:0"}
    await webhooks._confirm_booking_callback(_Env(), bot, "tok", conv, db, redis, cb)
    assert "expired" in captured[-1].lower()
