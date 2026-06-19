"""Regression test for the notification re-dispatch bug.

dispatch_pending() used to filter on is_read (never set), so it re-selected and
re-sent the same notifications on every poll. It now filters on sent_via being
empty and marks rows dispatched, so each notification is handled exactly once.
"""
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

import workers.notification_worker as nw
from app.db.models import Notification


@pytest.mark.asyncio
async def test_notifications_dispatched_once(db, monkeypatch):
    # Reuse the test session inside the worker, and stub the per-notification
    # dispatch so no bot lookup / Telegram call happens.
    @asynccontextmanager
    async def _fake_ctx():
        yield db

    async def _fake_dispatch(notif, session):
        return ["dashboard"]

    monkeypatch.setattr(nw, "get_db_context", _fake_ctx)
    monkeypatch.setattr(nw, "_dispatch_notification", _fake_dispatch)

    uid = uuid.uuid4()
    db.add_all([
        Notification(user_id=uid, notification_type="trial", title="t1", body="b1"),
        Notification(user_id=uid, notification_type="trial", title="t2", body="b2"),
    ])
    await db.flush()

    first = await nw.dispatch_pending()
    assert first == 2                      # both dispatched on the first pass

    second = await nw.dispatch_pending()
    assert second == 0                     # the bug would re-dispatch the same 2

    res = await db.execute(select(Notification))
    notifs = res.scalars().all()
    assert all(n.sent_via for n in notifs)     # marked dispatched
    assert all(n.is_read is False for n in notifs)  # user's read flag untouched


@pytest.mark.asyncio
async def test_orphaned_notification_not_looped(db, monkeypatch):
    # If no channel is usable (e.g. user deleted), the row must still be marked
    # so it can't loop forever.
    @asynccontextmanager
    async def _fake_ctx():
        yield db

    async def _no_channels(notif, session):
        return []

    monkeypatch.setattr(nw, "get_db_context", _fake_ctx)
    monkeypatch.setattr(nw, "_dispatch_notification", _no_channels)

    db.add(Notification(user_id=uuid.uuid4(), notification_type="x", title="o", body="b"))
    await db.flush()

    assert await nw.dispatch_pending() == 1
    assert await nw.dispatch_pending() == 0   # not re-selected
