# app/services/owner_bot_menu.py
"""Owner management menu on a business's OWN bot.

When the business owner messages their deployed bot (private chat), give them a
lightweight management surface — appointments, a daily snapshot, add-item help,
a storefront preview, and a link to the full dashboard — instead of the
customer AI. Customers never see this (the caller verifies ownership first).

Native actions that work right inside the bot are answered here. The full owner
console (/app/owner/) authenticates against the MASTER bot token, so a WebApp
button from a business bot can't log in — for that we link out to the platform
bot. The PUBLIC storefront needs no auth, so it opens as a WebApp directly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Booking, KnowledgeItem, KnowledgeItemType, TokenWallet

logger = get_logger(__name__)

_COMMANDS = {"/start", "/menu", "/owner", "/appointments", "/today", "/help", "/dashboard"}
_BUTTONS = {"📅 Appointments", "📊 Today", "📦 Add Product", "🔧 Add Service",
            "🚀 Dashboard", "❓ Help"}


def is_owner_command(text: str | None) -> bool:
    """Cheap, DB-free check so the webhook only runs an ownership lookup when the
    message actually looks like an owner command / menu tap."""
    if not text:
        return False
    t = text.strip()
    return t.lower() in _COMMANDS or t in _BUTTONS


def _base() -> str:
    return (settings.base_url or "https://api.ethiogram.com").rstrip("/")


def _menu_keyboard(slug: str | None) -> dict:
    rows = [
        [{"text": "📅 Appointments"}, {"text": "📊 Today"}],
        [{"text": "📦 Add Product"}, {"text": "🔧 Add Service"}],
    ]
    last = []
    if slug:
        last.append({"text": "🛍 Storefront", "web_app": {"url": f"{_base()}/app/store/?s={slug}"}})
    last.append({"text": "🚀 Dashboard"})
    rows.append(last)
    rows.append([{"text": "❓ Help"}])
    return {"keyboard": rows, "resize_keyboard": True}


def _dashboard_button() -> dict:
    return {"inline_keyboard": [[{
        "text": "Open Ethiogram Dashboard",
        "url": f"https://t.me/{settings.master_bot_username}?start=dashboard",
    }]]}


async def _appointments_text(db: AsyncSession, business) -> str:
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(Booking).where(
            Booking.business_id == business.id,
            Booking.status == "confirmed",
            Booking.starts_at >= now,
        ).order_by(Booking.starts_at.asc()).limit(10)
    )).scalars().all()
    if not rows:
        return "📅 No upcoming appointments.\nWhen customers book, they'll show here."
    try:
        zone = ZoneInfo(getattr(business, "timezone", None) or "Africa/Addis_Ababa")
    except Exception:
        zone = ZoneInfo("Africa/Addis_Ababa")
    lines = ["📅 <b>Upcoming appointments</b>"]
    for b in rows:
        start = b.starts_at if b.starts_at.tzinfo else b.starts_at.replace(tzinfo=timezone.utc)
        when = start.astimezone(zone).strftime("%a %d %b · %I:%M %p")
        who = b.customer_name or "Customer"
        svc = f" — {b.service_name}" if b.service_name else ""
        lines.append(f"• {when} · {who}{svc}")
    return "\n".join(lines)


async def _today_text(db: AsyncSession, business) -> str:
    now = datetime.now(timezone.utc)
    balance = await db.scalar(
        select(TokenWallet.balance).where(TokenWallet.business_id == business.id))
    upcoming = await db.scalar(
        select(func.count(Booking.id)).where(
            Booking.business_id == business.id,
            Booking.status == "confirmed",
            Booking.starts_at >= now,
        )) or 0
    products = await db.scalar(
        select(func.count(KnowledgeItem.id)).where(
            KnowledgeItem.business_id == business.id,
            KnowledgeItem.item_type == KnowledgeItemType.product,
            KnowledgeItem.is_active.is_(True))) or 0
    services = await db.scalar(
        select(func.count(KnowledgeItem.id)).where(
            KnowledgeItem.business_id == business.id,
            KnowledgeItem.item_type == KnowledgeItemType.service,
            KnowledgeItem.is_active.is_(True))) or 0
    return (
        f"📊 <b>{business.name} — snapshot</b>\n"
        f"💰 Wallet: {balance if balance is not None else 0} ETG\n"
        f"📅 Upcoming appointments: {upcoming}\n"
        f"📦 Catalog: {products} product(s), {services} service(s)"
    )


_ADD_PRODUCT_HELP = (
    "📦 <b>Add a product</b>\n"
    "Send me a <b>photo</b> with a caption like:\n\n"
    "Title: Blue Summer Dress\nPrice: 1200 ETB\nCategory: Dresses\n\n"
    "I'll add it to your store and website. Edit details later in your dashboard."
)
_ADD_SERVICE_HELP = (
    "🔧 <b>Add a service</b>\n"
    "Send me a message (photo optional) like:\n\n"
    "Title: Home Cleaning\nType: service\nPrice: 800 ETB\nDuration: 2 hours\n\n"
    "Services with a duration are bookable — customers can pick a time."
)
_HELP = (
    "🏪 <b>Owner menu</b>\n"
    "• 📅 Appointments — your upcoming bookings\n"
    "• 📊 Today — wallet, bookings & catalog snapshot\n"
    "• 📦 Add Product / 🔧 Add Service — grow your catalog\n"
    "• 🛍 Storefront — preview your public store\n"
    "• 🚀 Dashboard — full management in the Ethiogram app\n\n"
    "Tip: customers chatting with this bot get your AI assistant automatically."
)


async def handle(envelope, bot, business, raw_token, db: AsyncSession, redis) -> bool:
    """Dispatch an owner command/menu tap. Assumes the caller verified ownership
    and that this is a private chat. Returns True when it produced a reply."""
    from app.services.telegram_service import telegram_service
    if business is None:
        return False
    text = (envelope.text or "").strip()
    low = text.lower()
    chat = envelope.customer_id
    slug = getattr(business, "slug", None)

    if low in ("/start", "/menu", "/owner") or text == "🏪 Menu":
        name = getattr(business, "name", None) or "your business"
        await telegram_service.send_message(
            raw_token, chat,
            f"👋 Welcome back! Manage <b>{name}</b> right here.\nWhat would you like to do?",
            reply_markup=_menu_keyboard(slug))
        return True
    if low == "/appointments" or text == "📅 Appointments":
        await telegram_service.send_message(raw_token, chat, await _appointments_text(db, business))
        return True
    if low == "/today" or text == "📊 Today":
        await telegram_service.send_message(raw_token, chat, await _today_text(db, business))
        return True
    if text == "📦 Add Product":
        await telegram_service.send_message(raw_token, chat, _ADD_PRODUCT_HELP)
        return True
    if text == "🔧 Add Service":
        await telegram_service.send_message(raw_token, chat, _ADD_SERVICE_HELP)
        return True
    if low == "/dashboard" or text == "🚀 Dashboard":
        await telegram_service.send_message(
            raw_token, chat,
            "🚀 Open the full dashboard in the Ethiogram app:",
            reply_markup=_dashboard_button())
        return True
    if low == "/help" or text == "❓ Help":
        await telegram_service.send_message(raw_token, chat, _HELP)
        return True
    return False
