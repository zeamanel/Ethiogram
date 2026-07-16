"""Tests for the master/platform bot menu: language screen, main menu, commands."""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.services.platform_menu as pm
from app.db.models import Business, User, UserRole


@pytest.fixture
def sent(monkeypatch):
    """Capture telegram_service.send_message calls."""
    calls = []

    async def _send(token, chat_id, text, **kw):
        calls.append({"text": text, "reply_markup": kw.get("reply_markup")})
    from app.services.telegram_service import telegram_service
    monkeypatch.setattr(telegram_service, "send_message", _send)
    return calls


def _env(tg_id, text):
    return SimpleNamespace(customer_id=str(tg_id), customer_username="u",
                           customer_name="U", text=text, chat_type="private")


def _kb_text(markup):
    """Flatten a keyboard's button labels."""
    if not markup:
        return []
    rows = markup.get("keyboard") or markup.get("inline_keyboard") or []
    return [b["text"] for row in rows for b in row]


@pytest.mark.asyncio
async def test_start_shows_language_screen(db, mock_redis, sent):
    await pm.handle_platform_update(_env(111, "/start"), db, mock_redis)
    assert "language" in sent[-1]["text"].lower()
    labels = _kb_text(sent[-1]["reply_markup"])
    assert "🇬🇧 English" in labels and "🇸🇦 العربية" in labels


@pytest.mark.asyncio
async def test_language_pick_stores_and_shows_menu(db, mock_redis, sent):
    await pm.handle_platform_update(_env(222, "🇪🇸 Español"), db, mock_redis)
    # localized header + main menu
    assert "Bienvenido" in sent[-1]["text"]
    labels = _kb_text(sent[-1]["reply_markup"])
    assert "🚀 Dashboard" in labels and "🤖 Bot Gallery" in labels and "💬 Support" in labels
    # persisted
    user = (await db.execute(select(User).where(User.telegram_id == 222))).scalar_one()
    assert user.language_code == "es"


@pytest.mark.asyncio
async def test_admin_sees_admin_button(db, mock_redis, sent):
    db.add(User(id=uuid.uuid4(), telegram_id=333, role=UserRole.admin, is_admin=True, is_active=True))
    await db.flush()
    await pm.handle_platform_update(_env(333, "🇬🇧 English"), db, mock_redis)
    assert "⚙️ Admin Console" in _kb_text(sent[-1]["reply_markup"])


@pytest.mark.asyncio
async def test_non_admin_has_no_admin_button(db, mock_redis, sent):
    await pm.handle_platform_update(_env(444, "🇬🇧 English"), db, mock_redis)
    assert "⚙️ Admin Console" not in _kb_text(sent[-1]["reply_markup"])


@pytest.mark.asyncio
async def test_help_and_support(db, mock_redis, sent):
    await pm.handle_platform_update(_env(555, "/help"), db, mock_redis)
    assert "Dashboard" in sent[-1]["text"] and "/gallery" in sent[-1]["text"]
    await pm.handle_platform_update(_env(555, "💬 Support"), db, mock_redis)
    assert "support" in sent[-1]["text"].lower()


@pytest.mark.asyncio
async def test_gallery_command_opens_webapp(db, mock_redis, sent):
    await pm.handle_platform_update(_env(666, "/gallery"), db, mock_redis)
    mk = sent[-1]["reply_markup"]
    url = mk["inline_keyboard"][0][0]["web_app"]["url"]
    assert url.endswith("/app/gallery/")


@pytest.mark.asyncio
async def test_account_command_opens_webapp(db, mock_redis, sent):
    await pm.handle_platform_update(_env(661, "/account"), db, mock_redis)
    url = sent[-1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    assert url.endswith("/app/account/")


@pytest.mark.asyncio
async def test_privacy_command_links_policy(db, mock_redis, sent):
    await pm.handle_platform_update(_env(662, "/privacy"), db, mock_redis)
    urls = [b["url"] for b in sent[-1]["reply_markup"]["inline_keyboard"][0]]
    assert any(u.endswith("/app/legal/privacy.html") for u in urls)


@pytest.mark.asyncio
async def test_menu_includes_account_and_privacy(db, mock_redis, sent):
    await pm.handle_platform_update(_env(663, "🇬🇧 English"), db, mock_redis)
    labels = _kb_text(sent[-1]["reply_markup"])
    assert "👤 My Account" in labels and "🔐 Privacy" in labels


@pytest.mark.asyncio
async def test_view_store_uses_owner_business(db, mock_redis, sent):
    uid = uuid.uuid4()
    db.add(User(id=uid, telegram_id=777, role=UserRole.owner, is_active=True))
    db.add(Business(id=uuid.uuid4(), owner_id=uid, name="Selam", slug="selam"))
    await db.flush()
    await pm.handle_platform_update(_env(777, "🛍 View Store"), db, mock_redis)
    url = sent[-1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    assert url.endswith("/app/store/?s=selam")


@pytest.mark.asyncio
async def test_view_store_without_business(db, mock_redis, sent):
    await pm.handle_platform_update(_env(888, "🛍 View Store"), db, mock_redis)
    assert "don't have a store" in sent[-1]["text"].lower()
