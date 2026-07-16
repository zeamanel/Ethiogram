# workers/booking_reminders.py
"""
Appointment reminder dispatcher — the no-show reducer.

Scans confirmed upcoming bookings and messages the CUSTOMER (via the business's
own bot) a 24-hour and a 1-hour reminder, marking each as sent so it fires once.
Idempotent and safe to run on a frequent cron.

Run via Cloud Scheduler (every ~15 min recommended):
    python -m workers.booking_reminders
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.core.security import decrypt
from app.db.models import Booking, Bot, BotStatus, Business
from app.db.session import (
    connect_db, connect_redis, disconnect_db, disconnect_redis, get_db_context,
)
from app.services.telegram_service import telegram_service

configure_logging()
logger = get_logger(__name__)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def due_reminder(starts_at, now, reminder_24h_sent_at, reminder_1h_sent_at):
    """Which reminder (if any) is due for a booking. Within the final hour only
    the 1h reminder is eligible; between 1h and 24h only the 24h reminder. Past
    bookings get nothing. Returns '1h' | '24h' | None."""
    delta = _aware(starts_at) - now
    if delta <= timedelta(0):
        return None
    if delta <= timedelta(hours=1):
        return "1h" if reminder_1h_sent_at is None else None
    if delta <= timedelta(hours=24):
        return "24h" if reminder_24h_sent_at is None else None
    return None


def _format_when(starts_at: datetime, tz_name: str) -> str:
    try:
        zone = ZoneInfo(tz_name or "Africa/Addis_Ababa")
    except Exception:
        zone = ZoneInfo("Africa/Addis_Ababa")
    return _aware(starts_at).astimezone(zone).strftime("%a %d %b at %I:%M %p")


def reminder_text(kind: str, *, service: str, when: str, business: str) -> str:
    svc = service or "appointment"
    if kind == "1h":
        return (f"⏰ Reminder: your {svc} with {business} is in about an hour "
                f"({when}). See you soon!")
    return (f"⏰ Reminder: your {svc} with {business} is tomorrow — {when}. "
            f"Reply here if you need to reschedule.")


async def scan_and_send(db: AsyncSession, now: datetime | None = None) -> int:
    """Send any due reminders for confirmed bookings in the next 24h. Returns
    the number of reminders sent."""
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(
        select(Booking).where(
            Booking.status == "confirmed",
            Booking.starts_at > now,
            Booking.starts_at <= now + timedelta(hours=24),
        ).order_by(Booking.starts_at.asc())
    )).scalars().all()

    token_cache: dict = {}   # business_id -> (raw_token | None, business_name, tz)
    sent = 0
    for b in rows:
        kind = due_reminder(b.starts_at, now, b.reminder_24h_sent_at, b.reminder_1h_sent_at)
        if not kind:
            continue
        if await _send_reminder(db, b, kind, token_cache, now):
            if kind == "1h":
                b.reminder_1h_sent_at = now
            else:
                b.reminder_24h_sent_at = now
            sent += 1
    await db.flush()
    return sent


async def _business_ctx(db: AsyncSession, business_id, cache: dict):
    if business_id in cache:
        return cache[business_id]
    bot = (await db.execute(
        select(Bot).where(Bot.business_id == business_id, Bot.status == BotStatus.active).limit(1)
    )).scalar_one_or_none()
    biz = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
    token = None
    if bot is not None:
        try:
            token = decrypt(bot.encrypted_token)
        except Exception as exc:
            logger.warning("Reminder: token decrypt failed", business_id=str(business_id), error=str(exc))
    ctx = (token, (biz.name if biz else "us"), (biz.timezone if biz else "Africa/Addis_Ababa"))
    cache[business_id] = ctx
    return ctx


async def _send_reminder(db, booking, kind, cache, now) -> bool:
    token, biz_name, tz_name = await _business_ctx(db, booking.business_id, cache)
    if not token:
        return False
    text = reminder_text(
        kind,
        service=booking.service_name or "appointment",
        when=_format_when(booking.starts_at, tz_name),
        business=biz_name,
    )
    try:
        await telegram_service.send_message(token, booking.customer_platform_id, text)
        logger.info("Reminder sent", booking_id=str(booking.id), kind=kind)
        return True
    except Exception as exc:
        logger.warning("Reminder send failed", booking_id=str(booking.id), error=str(exc))
        return False


async def run() -> None:
    await connect_db()
    await connect_redis()
    try:
        async with get_db_context() as db:
            count = await scan_and_send(db)
        logger.info("Booking reminders complete", sent=count)
    finally:
        await disconnect_db()
        await disconnect_redis()


if __name__ == "__main__":
    asyncio.run(run())
