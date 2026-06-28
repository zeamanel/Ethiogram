# app/services/platform_menu.py
"""Master/platform bot UX — language screen + persistent main menu + commands.

This is the conversational layer for the platform bot (@ethiogramchat_bot),
NOT the business AI bots. It's driven from the webhook (which detects the master
bot by token hash). Menu buttons are a Telegram ReplyKeyboardMarkup: WebApp
buttons open the Mini Apps directly; plain-text buttons send their label back,
which we handle here.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Business, User, UserRole

logger = get_logger(__name__)

# (flag label, language code) — the language screen.
LANGUAGES = [
    ("🇬🇧 English", "en"), ("🇪🇸 Español", "es"), ("🇫🇷 Français", "fr"),
    ("🇷🇺 Русский", "ru"), ("🇨🇳 中文", "zh"), ("🇸🇦 العربية", "ar"),
]
_LANG_BY_LABEL = {label: code for label, code in LANGUAGES}

# Minimal localized headers (full i18n of every string is a follow-up).
_WELCOME = {"en": "Welcome to Ethiogram 👋", "es": "Bienvenido a Ethiogram 👋",
            "fr": "Bienvenue sur Ethiogram 👋", "ru": "Добро пожаловать в Ethiogram 👋",
            "zh": "欢迎使用 Ethiogram 👋", "ar": "مرحبًا بك في Ethiogram 👋"}
_MENU = {"en": "Main menu", "es": "Menú principal", "fr": "Menu principal",
         "ru": "Главное меню", "zh": "主菜单", "ar": "القائمة الرئيسية"}

_SUPPORT = "💬 Need help? Contact @ethiogram_support — we usually reply within a few hours."
_HELP = ("Ethiogram lets you run an AI business bot on Telegram.\n\n"
         "• 🚀 Dashboard — manage your business, catalog & storefront\n"
         "• 🤖 Bot Gallery — discover other businesses\n"
         "• 🛍 View Store — your public storefront\n\n"
         "Commands: /start  /help  /gallery  /dashboard")


def _base() -> str:
    return (settings.base_url or "https://api.ethiogram.com").rstrip("/")


def _lang_keyboard() -> dict:
    rows = []
    for i in range(0, len(LANGUAGES), 2):
        row = [{"text": LANGUAGES[i][0]}]
        if i + 1 < len(LANGUAGES):
            row.append({"text": LANGUAGES[i + 1][0]})
        rows.append(row)
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}


def _main_menu(is_admin: bool) -> dict:
    base = _base()
    rows = [
        [{"text": "🚀 Dashboard", "web_app": {"url": f"{base}/app/owner/"}},
         {"text": "📊 My Businesses", "web_app": {"url": f"{base}/app/owner/"}}],
        [{"text": "🤖 Bot Gallery", "web_app": {"url": f"{base}/app/gallery/"}},
         {"text": "🛍 View Store"}],
        [{"text": "💬 Support"}, {"text": "❓ Help"}],
    ]
    if is_admin:
        rows.append([{"text": "⚙️ Admin Console", "web_app": {"url": f"{base}/app/owner/"}}])
    return {"keyboard": rows, "resize_keyboard": True}


def _open_button(label: str, path: str) -> dict:
    return {"inline_keyboard": [[{"text": label, "web_app": {"url": f"{_base()}{path}"}}]]}


async def _resolve_user(envelope, db: AsyncSession) -> "User | None":
    """Find-or-create the platform user by telegram id."""
    try:
        tg_id = int(envelope.customer_id)
    except (TypeError, ValueError):
        return None
    user = (await db.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
    if user is None:
        user = User(telegram_id=tg_id, username=envelope.customer_username,
                    full_name=envelope.customer_name, role=UserRole.owner,
                    language_code="en", is_verified=True)
        db.add(user)
        await db.flush()
    return user


def _is_admin(user) -> bool:
    if user is None:
        return False
    return bool(getattr(user, "is_admin", False)
                or user.role == UserRole.admin
                or (user.telegram_id and user.telegram_id in (settings.admin_telegram_ids or [])))


async def handle_platform_update(envelope, db: AsyncSession, redis) -> None:
    """Route a master-bot message to the right menu/command response."""
    from app.services.telegram_service import telegram_service
    token = settings.master_bot_token
    chat = envelope.customer_id
    text = (envelope.text or "").strip()
    low = text.lower()
    user = await _resolve_user(envelope, db)
    lang = (user.language_code if user and user.language_code else "en")

    # /start, /lang → language screen
    if low in ("/start", "/lang", "/language", "/menu"):
        await telegram_service.send_message(
            token, chat, "🌍 Welcome! Choose your language:", reply_markup=_lang_keyboard())
        return

    # language picked → store + show the main menu (localized header)
    if text in _LANG_BY_LABEL:
        lang = _LANG_BY_LABEL[text]
        if user:
            user.language_code = lang
        await telegram_service.send_message(
            token, chat, f"{_WELCOME.get(lang, _WELCOME['en'])}\n{_MENU.get(lang, 'Main menu')}:",
            reply_markup=_main_menu(_is_admin(user)))
        return

    # commands + text menu buttons
    if low == "/help" or text == "❓ Help":
        await telegram_service.send_message(token, chat, _HELP)
        return
    if text == "💬 Support":
        await telegram_service.send_message(token, chat, _SUPPORT)
        return
    if low == "/gallery" or text == "🤖 Bot Gallery":
        await telegram_service.send_message(
            token, chat, "🤖 Discover businesses on Ethiogram:",
            reply_markup=_open_button("Open Bot Gallery", "/app/gallery/"))
        return
    if low == "/dashboard" or text in ("🚀 Open Dashboard", "🚀 Dashboard", "📊 My Businesses", "⚙️ Admin Console"):
        await telegram_service.send_message(
            token, chat, "🚀 Open your dashboard:",
            reply_markup=_open_button("Open Dashboard", "/app/owner/"))
        return
    if text in ("🛍 View Store", "🛍️ View Store"):
        slug = None
        if user:
            slug = await db.scalar(
                select(Business.slug).where(
                    Business.owner_id == user.id, Business.deleted_at.is_(None)
                ).limit(1))
        if slug:
            await telegram_service.send_message(
                token, chat, "🛍 Your public store:",
                reply_markup=_open_button("Open Store", f"/app/store/?s={slug}"))
        else:
            await telegram_service.send_message(
                token, chat,
                "You don't have a store yet — open the Dashboard to create your business first.")
        return

    # anything else → re-show the main menu
    await telegram_service.send_message(
        token, chat, f"{_MENU.get(lang, 'Main menu')}:", reply_markup=_main_menu(_is_admin(user)))
