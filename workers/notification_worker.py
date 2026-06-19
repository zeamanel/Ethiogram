# workers/notification_worker.py
"""
Notification dispatcher.

Processes the notifications table for unread/unsent items and
dispatches them via configured channels (Telegram bot message,
in-app dashboard flag).

Run continuously or once:
    python -m workers.notification_worker [once|continuous]
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.db.models import Bot, BotStatus, Business, Notification, User
from app.db.session import connect_db, disconnect_db, connect_redis, disconnect_redis, get_db_context
from app.services.telegram_service import telegram_service
from app.core.security import decrypt

configure_logging()
logger = get_logger(__name__)

_BATCH_SIZE = 50
_POLL_INTERVAL = 15  # seconds


async def dispatch_pending() -> int:
    """Dispatch up to _BATCH_SIZE not-yet-finished notifications.

    Selection is on ``dispatched_at IS NULL`` — the terminal flag set once every
    intended channel has been delivered — NOT on ``is_read`` (the user's
    dashboard read flag, which was the old re-dispatch bug). Per channel:
      1. only channels not already in ``sent_via`` are attempted,
      2. on success the channel is appended to ``sent_via``,
      3. once all intended channels are recorded, ``dispatched_at`` is set so
         the row drops out of future polls.
    A channel that fails (e.g. a Telegram outage) is NOT marked, so the row
    stays selectable and is retried on the next poll. Returns the count of
    notifications that became fully dispatched this pass.
    """
    dispatched = 0

    async with get_db_context() as db:
        result = await db.execute(
            select(Notification)
            .where(Notification.dispatched_at.is_(None))
            .order_by(Notification.created_at.asc())
            .limit(_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        notifications = result.scalars().all()

        if not notifications:
            return 0

        for notif in notifications:
            try:
                newly_sent, fully_done = await _dispatch_notification(notif, db)
                if newly_sent:
                    notif.sent_via = list(set((notif.sent_via or []) + newly_sent))
                if fully_done:
                    notif.dispatched_at = datetime.now(timezone.utc)
                    dispatched += 1
            except Exception as exc:
                logger.error(
                    "Notification dispatch failed",
                    notification_id=str(notif.id),
                    error=str(exc),
                )

    return dispatched


async def _dispatch_notification(notif: Notification, db: AsyncSession) -> tuple[list[str], bool]:
    """Deliver any not-yet-sent intended channels for one notification.

    Returns ``(newly_sent_channels, fully_done)`` where ``fully_done`` is True
    once every intended channel has been delivered (so the caller can set the
    terminal ``dispatched_at``).
    """
    already = set(notif.sent_via or [])
    newly: list[str] = []

    user_result = await db.execute(select(User).where(User.id == notif.user_id))
    user = user_result.scalar_one_or_none()

    # Intended channels for this notification.
    intended = {"dashboard"}  # always — a DB flag the dashboard reads
    if user is not None and user.telegram_id:
        intended.add("telegram")

    # Telegram: best-effort. Not appended on failure, so it's retried next poll.
    if "telegram" in intended and "telegram" not in already:
        if await _send_telegram(user, notif, db):
            newly.append("telegram")

    # Dashboard: always succeeds (nothing to send; it's just the flag).
    if "dashboard" not in already:
        newly.append("dashboard")

    delivered = already | set(newly)
    fully_done = intended.issubset(delivered)
    return newly, fully_done


async def _send_telegram(
    user: User, notif: Notification, db: AsyncSession
) -> bool:
    """Find the user's first active bot and send the notification via it."""
    bot_result = await db.execute(
        select(Bot)
        .join(Bot.business)
        .where(
            Business.owner_id == user.id,
            Bot.status == BotStatus.active,
        )
        .limit(1)
    )
    bot = bot_result.scalar_one_or_none()
    if bot is None:
        return False

    try:
        raw_token = decrypt(bot.encrypted_token)
        text = f"<b>{notif.title}</b>\n\n{notif.body}"
        await telegram_service.send_message(raw_token, user.telegram_id, text)
        logger.info(
            "Telegram notification sent",
            notification_id=str(notif.id),
            user_id=str(user.id),
            type=notif.notification_type,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Telegram notification failed",
            notification_id=str(notif.id),
            error=str(exc),
        )
        return False


async def create_notification(
    user_id,
    notification_type: str,
    title: str,
    body: str,
    data: dict | None = None,
    db: AsyncSession = None,
) -> None:
    """
    Helper called by other services to queue a notification.
    If db is provided it uses it directly; otherwise opens its own context.
    """
    notif = Notification(
        user_id=str(user_id),
        notification_type=notification_type,
        title=title,
        body=body,
        data=data or {},
        sent_via=[],
        is_read=False,
    )
    if db:
        db.add(notif)
    else:
        async with get_db_context() as ctx_db:
            ctx_db.add(notif)


async def run_once() -> None:
    await connect_db()
    await connect_redis()
    try:
        count = await dispatch_pending()
        logger.info("Notification worker run complete", dispatched=count)
    finally:
        await disconnect_db()
        await disconnect_redis()


async def run_continuous() -> None:
    await connect_db()
    await connect_redis()
    logger.info(f"Notification worker started (poll: {_POLL_INTERVAL}s)")
    try:
        while True:
            try:
                await dispatch_pending()
            except Exception as exc:
                logger.error("Notification loop error", error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)
    finally:
        await disconnect_db()
        await disconnect_redis()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "continuous":
        asyncio.run(run_continuous())
    else:
        asyncio.run(run_once())
