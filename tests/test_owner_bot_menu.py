"""Owner management menu on a business's own bot (owner-only, native actions)."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.services.owner_bot_menu as obm
from app.db.models import (
    Booking, Business, KnowledgeItem, KnowledgeItemType, TokenWallet, User,
)

UTC = timezone.utc


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def _send(token, chat_id, text, **kw):
        calls.append({"text": text, "reply_markup": kw.get("reply_markup")})
    from app.services.telegram_service import telegram_service
    monkeypatch.setattr(telegram_service, "send_message", _send)
    return calls


def _env(text):
    return SimpleNamespace(customer_id="4242", customer_name="Owner", text=text,
                           chat_type="private")


async def _biz(db):
    owner = User(id=uuid.uuid4(), telegram_id=4242, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="Selam Salon",
                   slug="selam", timezone="UTC")
    db.add(biz)
    await db.flush()
    return biz


def _labels(markup):
    rows = (markup or {}).get("keyboard") or (markup or {}).get("inline_keyboard") or []
    return [b["text"] for row in rows for b in row]


# ── trigger gate ─────────────────────────────────────────────────────────────

def test_is_owner_command():
    assert obm.is_owner_command("/start") and obm.is_owner_command("📅 Appointments")
    assert obm.is_owner_command("📦 Add Product")
    assert not obm.is_owner_command("how much is a haircut?")
    assert not obm.is_owner_command("") and not obm.is_owner_command(None)


# ── menu + actions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_shows_owner_menu(db, sent):
    biz = await _biz(db)
    assert await obm.handle(_env("/start"), bot=None, business=biz, raw_token="t", db=db, redis=None)
    labels = _labels(sent[-1]["reply_markup"])
    assert "📅 Appointments" in labels and "📦 Add Product" in labels and "🚀 Dashboard" in labels
    # storefront opens the public store as a WebApp
    rows = sent[-1]["reply_markup"]["keyboard"]
    store = next((b for row in rows for b in row if b["text"] == "🛍 Storefront"), None)
    assert store and store["web_app"]["url"].endswith("/app/store/?s=selam")


@pytest.mark.asyncio
async def test_appointments_lists_bookings(db, sent):
    biz = await _biz(db)
    start = datetime.now(UTC) + timedelta(days=1)
    db.add(Booking(id=uuid.uuid4(), business_id=biz.id, customer_platform_id="9",
                   customer_name="Sara", service_name="Haircut",
                   starts_at=start, ends_at=start + timedelta(hours=1), status="confirmed"))
    db.add(Booking(id=uuid.uuid4(), business_id=biz.id, customer_platform_id="9",
                   customer_name="Old", starts_at=datetime.now(UTC) - timedelta(days=1),
                   ends_at=datetime.now(UTC), status="confirmed"))   # past → excluded
    await db.flush()
    await obm.handle(_env("📅 Appointments"), bot=None, business=biz, raw_token="t", db=db, redis=None)
    txt = sent[-1]["text"]
    assert "Sara" in txt and "Haircut" in txt and "Old" not in txt


@pytest.mark.asyncio
async def test_appointments_empty(db, sent):
    biz = await _biz(db)
    await obm.handle(_env("/appointments"), bot=None, business=biz, raw_token="t", db=db, redis=None)
    assert "No upcoming appointments" in sent[-1]["text"]


@pytest.mark.asyncio
async def test_today_snapshot(db, sent):
    biz = await _biz(db)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=4200))
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.product,
                         title="Mug", is_active=True))
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.service,
                         title="Cut", is_active=True))
    await db.flush()
    await obm.handle(_env("📊 Today"), bot=None, business=biz, raw_token="t", db=db, redis=None)
    txt = sent[-1]["text"]
    assert "4200 ETG" in txt and "1 product" in txt and "1 service" in txt


@pytest.mark.asyncio
async def test_add_product_and_service_help(db, sent):
    biz = await _biz(db)
    await obm.handle(_env("📦 Add Product"), bot=None, business=biz, raw_token="t", db=db, redis=None)
    assert "photo" in sent[-1]["text"].lower() and "Price:" in sent[-1]["text"]
    await obm.handle(_env("🔧 Add Service"), bot=None, business=biz, raw_token="t", db=db, redis=None)
    assert "Duration:" in sent[-1]["text"] and "bookable" in sent[-1]["text"]


@pytest.mark.asyncio
async def test_dashboard_links_to_platform_bot(db, sent):
    biz = await _biz(db)
    await obm.handle(_env("🚀 Dashboard"), bot=None, business=biz, raw_token="t", db=db, redis=None)
    url = sent[-1]["reply_markup"]["inline_keyboard"][0][0]["url"]
    assert url.startswith("https://t.me/")


@pytest.mark.asyncio
async def test_unknown_owner_text_falls_through(db, sent):
    biz = await _biz(db)
    handled = await obm.handle(_env("random question"), bot=None, business=biz,
                               raw_token="t", db=db, redis=None)
    assert handled is False and sent == []
