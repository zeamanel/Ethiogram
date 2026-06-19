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
    """Dispatch up to _BATCH_SIZE not-yet-dispatched notifications.

    "Pending" means ``sent_via`` is still empty — NOT ``is_read``. ``is_read`` is
    the user's dashboard read flag (paired with ``read_at``); using it here was
    the re-dispatch bug: nothing ever set it, so the same rows were re-selected
    forever. Once a notification is dispatched we populate ``sent_via`` (which is
    committed on context exit), so it drops out of the next batch.
    Returns the count dispatched.
    """
    dispatched = 0

    async with get_db_context() as db:
        result = await db.execute(
            select(Notification)
            .where(Notification.sent_via == [])
            .order_by(Notification.created_at.asc())
            .limit(_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        notifications = result.scalars().all()

        if not notifications:
            return 0

        for notif in notifications:
            try:
                sent_channels = await _dispatch_notification(notif, db)
                # Always mark processed so the row can't be re-selected. If no
                # channel was usable (e.g. orphaned user), record "dashboard" so
                # it still drops out of the queue instead of looping forever.
                notif.sent_via = list(set((notif.sent_via or []) + (sent_channels or ["dashboard"])))
                dispatched += 1
            except Exception as exc:
                logger.error(
                    "Notification dispatch failed",
                    notification_id=str(notif.id),
                    error=str(exc),
                )

    return dispatched


async def _dispatch_notification(notif: Notification, db: AsyncSession) -> list[str]:
    """Dispatch a single notification. Returns list of channels used."""
    sent: list[str] = []

    user_result = await db.execute(select(User).where(User.id == notif.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return sent

    # Telegram channel
    if user.telegram_id and "telegram" not in (notif.sent_via or []):
        tg_sent = await _send_telegram(user, notif, db)
        if tg_sent:
            sent.append("telegram")

    # Dashboard is always marked (it's just a DB flag read by the frontend)
    if "dashboard" not in (notif.sent_via or []):
        sent.append("dashboard")

    return sent


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
