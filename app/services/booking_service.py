# app/services/booking_service.py
"""Native appointment availability + persistence.

This is the credential-free booking engine: availability is computed from the
business's hours + slot length minus the appointments already in OUR database,
so a non-technical owner can take bookings without wiring Google Calendar.
(The Concierge's Google Calendar path still exists as an optional mirror.)

The slot maths is a pure function (`compute_free_slots`) so it is trivially
unit-testable; the async helpers load existing bookings and persist new ones.
"""
from __future__ import annotations

import re
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TZ = "Africa/Addis_Ababa"
DEFAULT_OPEN_HOUR = 9
DEFAULT_CLOSE_HOUR = 17
DEFAULT_DURATION_MIN = 60


def _tz(name) -> ZoneInfo:
    if isinstance(name, ZoneInfo):
        return name
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Treat naive datetimes (e.g. from SQLite) as UTC so comparisons are safe."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def parse_duration_minutes(text, default: int = DEFAULT_DURATION_MIN) -> int:
    """Best-effort parse of a free-text duration into minutes.

    Handles "30 min", "45 minutes", "1 hour", "2 hours", "1h30m", "1.5 hours",
    and a bare number (treated as minutes). Falls back to ``default``."""
    if text is None:
        return default
    if isinstance(text, (int, float)):
        return max(5, int(text))
    s = str(text).strip().lower()
    if not s:
        return default
    if re.fullmatch(r"\d+", s):                       # bare number → minutes
        return max(5, int(s))
    total, found = 0.0, False
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)", s)
    if h:
        total += float(h.group(1)) * 60
        found = True
    m = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes)", s)
    if m:
        total += int(m.group(1))
        found = True
    if not found:
        f = re.fullmatch(r"\d+(?:\.\d+)?", s)         # bare float → hours
        if f:
            total, found = float(s) * 60, True
    return max(5, int(round(total))) if found else default


async def load_bookable_services(db: AsyncSession, business_id, limit: int = 8) -> list[dict]:
    """Bookable services from the catalog (KnowledgeItem type=service), each with
    its own duration parsed from the item data. Empty when the business hasn't
    listed services — the caller then falls back to a single default duration."""
    from app.db.models import KnowledgeItem, KnowledgeItemType

    rows = (await db.execute(
        select(KnowledgeItem.title, KnowledgeItem.data).where(
            KnowledgeItem.business_id == business_id,
            KnowledgeItem.item_type == KnowledgeItemType.service,
            KnowledgeItem.is_active.is_(True),
        ).order_by(KnowledgeItem.created_at.asc()).limit(limit)
    )).all()
    out = []
    for title, data in rows:
        d = data or {}
        if not title:
            continue
        out.append({
            "name": title[:64],
            "price": d.get("price"),
            "duration_min": parse_duration_minutes(d.get("duration")),
        })
    return out


def booking_config(child_data: Optional[dict]) -> dict:
    """Resolve the business's booking window from its Concierge child_data,
    falling back to sensible defaults. Hours are clamped to valid values."""
    cd = child_data or {}

    def _int(key: str, default: int) -> int:
        try:
            return int(cd.get(key, default))
        except (TypeError, ValueError):
            return default

    open_hour = min(23, max(0, _int("open_hour", DEFAULT_OPEN_HOUR)))
    close_hour = min(23, max(open_hour + 1, _int("close_hour", DEFAULT_CLOSE_HOUR)))
    return {
        "tz": cd.get("timezone") or DEFAULT_TZ,
        "open_hour": open_hour,
        "close_hour": close_hour,
        "duration": max(5, _int("appointment_duration_minutes", DEFAULT_DURATION_MIN)),
    }


def compute_free_slots(
    day,
    *,
    busy: list[tuple[datetime, datetime]],
    tz,
    open_hour: int = DEFAULT_OPEN_HOUR,
    close_hour: int = DEFAULT_CLOSE_HOUR,
    duration_minutes: int = DEFAULT_DURATION_MIN,
    num_slots: int = 5,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Pure slot computation. ``day`` is a date (or datetime) in ``tz``; returns
    up to ``num_slots`` open slots that don't overlap any ``busy`` interval and
    aren't in the past relative to ``now``. Each slot is {start, end, label}."""
    zone = _tz(tz)
    if isinstance(day, datetime):
        day = (day.astimezone(zone).date() if day.tzinfo else day.date())
    elif not isinstance(day, date_cls):
        raise TypeError("day must be a date or datetime")

    norm_busy = [(_aware(b0), _aware(b1)) for (b0, b1) in busy]
    now = _aware(now)

    day_start = datetime.combine(day, time(hour=open_hour), tzinfo=zone)
    day_end = datetime.combine(day, time(hour=close_hour), tzinfo=zone)
    step = timedelta(minutes=duration_minutes)

    slots: list[dict] = []
    cursor = day_start
    while cursor + step <= day_end and len(slots) < num_slots:
        slot_end = cursor + step
        in_past = now is not None and cursor <= now
        conflict = any(b0 < slot_end and b1 > cursor for (b0, b1) in norm_busy)
        if not in_past and not conflict:
            slots.append({
                "start": cursor.isoformat(),
                "end": slot_end.isoformat(),
                "label": cursor.strftime("%a %I:%M %p"),
            })
        cursor += step
    return slots


async def available_slots(
    db: AsyncSession,
    business_id,
    child_data: Optional[dict],
    day,
    num_slots: int = 5,
    now: Optional[datetime] = None,
    duration_minutes: Optional[int] = None,
) -> list[dict]:
    """Open slots for ``business_id`` on ``day``, computed natively from the
    business's hours minus its confirmed bookings that day. ``duration_minutes``
    overrides the business default (e.g. the chosen service's length)."""
    from app.db.models import Booking

    cfg = booking_config(child_data)
    duration = duration_minutes or cfg["duration"]
    zone = _tz(cfg["tz"])
    if isinstance(day, datetime):
        target_date = day.astimezone(zone).date() if day.tzinfo else day.date()
    else:
        target_date = day

    day_start = datetime.combine(target_date, time(0, 0), tzinfo=zone)
    day_end = day_start + timedelta(days=1)
    rows = (await db.execute(
        select(Booking.starts_at, Booking.ends_at).where(
            Booking.business_id == business_id,
            Booking.status == "confirmed",
            Booking.starts_at >= day_start,
            Booking.starts_at < day_end,
        )
    )).all()
    busy = [(s, e) for (s, e) in rows]

    return compute_free_slots(
        target_date, busy=busy, tz=zone,
        open_hour=cfg["open_hour"], close_hour=cfg["close_hour"],
        duration_minutes=duration, num_slots=num_slots, now=now,
    )


async def create_booking(
    db: AsyncSession,
    *,
    business_id,
    customer_platform_id,
    starts_at: datetime,
    ends_at: datetime,
    conversation_id=None,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
    service_name: Optional[str] = None,
    price: Optional[str] = None,
    source: str = "telegram",
    notes: Optional[str] = None,
    calendar_event_id: Optional[str] = None,
):
    """Insert a confirmed Booking, guarding against an overlapping confirmed
    booking for the same business. Returns (booking, created: bool); on an
    overlap returns (None, False) so the caller can offer another slot."""
    from app.db.models import Booking

    conflict = await db.scalar(
        select(Booking.id).where(
            Booking.business_id == business_id,
            Booking.status == "confirmed",
            Booking.starts_at < ends_at,
            Booking.ends_at > starts_at,
        ).limit(1)
    )
    if conflict:
        return None, False

    booking = Booking(
        business_id=business_id,
        conversation_id=conversation_id,
        customer_platform_id=str(customer_platform_id),
        customer_name=customer_name,
        customer_phone=customer_phone,
        service_name=service_name,
        starts_at=starts_at,
        ends_at=ends_at,
        price=price,
        source=source,
        notes=notes,
        calendar_event_id=calendar_event_id,
        status="confirmed",
    )
    db.add(booking)
    await db.flush()
    return booking, True
