"""Regression tests for notification dispatch.

dispatch_pending() used to filter on is_read (never set), re-sending the same
notifications forever. It now selects on dispatched_at IS NULL, appends each
delivered channel to sent_via, and sets the terminal dispatched_at only once all
intended channels are delivered — so a failed Telegram send is retried while a
finished notification drops out.
"""
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

import workers.notification_worker as nw
from app.db.models import Notification, User


def _patch_ctx(db, monkeypatch):
    @asynccontextmanager
    async def _fake_ctx():
        yield db
    monkeypatch.setattr(nw, "get_db_context", _fake_ctx)


async def _add_user(db, telegram_id=None):
    u = User(email=f"{uuid.uuid4().hex}@x.com", telegram_id=telegram_id)
    db.add(u)
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_dashboard_only_dispatched_once(db, monkeypatch):
    _patch_ctx(db, monkeypatch)
    user = await _add_user(db, telegram_id=None)  # no telegram -> dashboard only
    db.add(Notification(user_id=user.id, notification_type="trial", title="t", body="b"))
    await db.flush()

    assert await nw.dispatch_pending() == 1
    assert await nw.dispatch_pending() == 0   # terminal flag set -> not re-selected

    n = (await db.execute(select(Notification))).scalars().one()
    assert n.dispatched_at is not None
    assert "dashboard" in n.sent_via
    assert n.is_read is False                 # user's read flag untouched


@pytest.mark.asyncio
async def test_orphaned_notification_marked_done(db, monkeypatch):
    _patch_ctx(db, monkeypatch)
    # user_id points at a nonexistent user -> only dashboard, still terminal.
    db.add(Notification(user_id=uuid.uuid4(), notification_type="x", title="o", body="b"))
    await db.flush()

    assert await nw.dispatch_pending() == 1
    assert await nw.dispatch_pending() == 0

    n = (await db.execute(select(Notification))).scalars().one()
    assert n.dispatched_at is not None


@pytest.mark.asyncio
async def test_failed_telegram_is_retried_then_completes(db, monkeypatch):
    _patch_ctx(db, monkeypatch)
    user = await _add_user(db, telegram_id=123456789)
    db.add(Notification(user_id=user.id, notification_type="alert", title="t", body="b"))
    await db.flush()

    # First Telegram attempt fails, second succeeds.
    attempts = {"n": 0}

    async def _send(user_, notif_, db_):
        attempts["n"] += 1
        return attempts["n"] >= 2

    monkeypatch.setattr(nw, "_send_telegram", _send)

    # Pass 1: telegram fails -> not fully done, stays selectable.
    assert await nw.dispatch_pending() == 0
    n = (await db.execute(select(Notification))).scalars().one()
    assert n.dispatched_at is None
    assert n.sent_via == ["dashboard"]        # dashboard delivered, telegram not

    # Pass 2: telegram retried and succeeds -> fully done.
    assert await nw.dispatch_pending() == 1
    n = (await db.execute(select(Notification))).scalars().one()
    assert n.dispatched_at is not None
    assert set(n.sent_via) == {"dashboard", "telegram"}
    assert attempts["n"] == 2                  # retried exactly once

    # Pass 3: nothing left.
    assert await nw.dispatch_pending() == 0
