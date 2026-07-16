# app/api/miniapp.py
"""Public customer-storefront API — one data-driven engine for every business.

GET /miniapp/{slug} returns a single combined payload (theme + layout + content)
assembled from the business, its mini_app_config, its knowledge_items, and its
bot. It is PUBLIC (no auth): the storefront is a public catalog, ordering happens
by deep-linking to the business bot, and the cart is client-side only.

The payload is cached in Redis (busted when the catalog changes) so the hot path
is a single key read.
"""
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, RateLimitError
from app.core.logging import get_logger
from app.utils.geo import directions_url as _directions_url
from app.db.models import (
    Bot,
    BotStatus,
    Business,
    KnowledgeItem,
    KnowledgeItemType,
    MiniAppConfig,
    UsageEvent,
)
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(prefix="/miniapp", tags=["miniapp"])

_CACHE_TTL = 300                       # seconds
_CACHE_KEY = "miniapp:slug:{slug}"

# Base storefront theme. Per-business values layer on top in this order:
#   defaults < business.brand_* < mini_app_config columns < layout_config.theme_overrides
_THEME_DEFAULTS = {
    "primary": "#E8421A", "secondary": "#FFFFFF", "accent": "#F5A623",
    "bg": "#0F0D0C", "text": "#F5F0EB", "surface": "#1A1714",
    "border": "rgba(255,255,255,0.07)",
    "font_heading": "Syne", "font_body": "DM Sans", "radius": "16px",
}

# Default section order when a business hasn't customised its layout.
_DEFAULT_SECTIONS = [
    {"type": "hero", "visible": True, "order": 0},
    {"type": "categories", "visible": True, "order": 1},
    {"type": "products", "visible": True, "order": 2},
    {"type": "services", "visible": True, "order": 3},
    {"type": "menu", "visible": True, "order": 4},
    {"type": "hours", "visible": False, "order": 5},
    {"type": "contact", "visible": True, "order": 6},
    {"type": "chat", "visible": True, "order": 7},
]


# ── assembly helpers ─────────────────────────────────────────────────────────

def _theme(cfg: Optional[MiniAppConfig], business: Business) -> dict:
    theme = dict(_THEME_DEFAULTS)
    if business.brand_primary_color:
        theme["primary"] = business.brand_primary_color
    if business.brand_secondary_color:
        theme["secondary"] = business.brand_secondary_color
    if cfg:
        theme["primary"] = cfg.theme_primary or theme["primary"]
        theme["secondary"] = cfg.theme_secondary or theme["secondary"]
        theme["accent"] = cfg.theme_accent or theme["accent"]
        # Fonts: dedicated columns win; font_family is the legacy body fallback.
        theme["font_heading"] = cfg.font_heading or theme["font_heading"]
        theme["font_body"] = cfg.font_body or cfg.font_family or theme["font_body"]
        # AI/owner overrides live in the existing JSONB (no dedicated column).
        overrides = (cfg.layout_config or {}).get("theme_overrides") or {}
        theme.update({k: v for k, v in overrides.items() if k in _THEME_DEFAULTS})
    return theme


def _sections(cfg: Optional[MiniAppConfig]) -> list[dict]:
    raw = None
    if cfg and isinstance(cfg.layout_config, dict):
        raw = cfg.layout_config.get("sections")
    if not isinstance(raw, list) or not raw:
        raw = _DEFAULT_SECTIONS
    visible = [s for s in raw if s.get("visible", True)]
    visible.sort(key=lambda s: s.get("order", 0))
    return [{"type": s["type"], "visible": True, "order": s.get("order", 0)}
            for s in visible if s.get("type")]


def _group_menu(menu_items: list[dict]) -> list[dict]:
    groups: dict[str, list] = {}
    for m in menu_items:
        groups.setdefault(m.get("category") or "Menu", []).append(m)
    return [{"category": c, "items": v} for c, v in groups.items()]


def _content(business: Business, bot: Optional[Bot], cfg: Optional[MiniAppConfig],
             items: list[KnowledgeItem]) -> dict:
    products, services, menu_items, faqs = [], [], [], []
    categories: list[str] = []
    for it in items:
        d = it.data or {}
        base = {"id": str(it.id), "title": it.title, "body": it.body, "price": d.get("price")}
        if it.item_type == KnowledgeItemType.product:
            cat = d.get("category")
            products.append({**base, "category": cat, "image_url": d.get("image_url")})
            if cat and cat not in categories:
                categories.append(cat)
        elif it.item_type == KnowledgeItemType.service:
            services.append({**base, "duration": d.get("duration")})
        elif it.item_type == KnowledgeItemType.menu_item:
            menu_items.append({**base, "category": d.get("category")})
        elif it.item_type == KnowledgeItemType.faq:
            faqs.append({"q": it.title, "a": it.body})

    layout_cfg = (cfg.layout_config or {}) if cfg else {}
    bot_username = bot.bot_username if bot else None
    return {
        "hero": {
            "title": business.name,
            "subtitle": layout_cfg.get("tagline") or business.description,
            "image_url": cfg.hero_image_url if cfg else None,
            "cta": layout_cfg.get("hero_cta") or "Order on Telegram",
        },
        "categories": categories,
        "products": products,
        "services": services,
        "menu": _group_menu(menu_items),
        "faqs": faqs,
        "contact": {
            "phone": business.phone,
            "email": business.email,
            "address": business.address,
            "latitude": business.latitude,
            "longitude": business.longitude,
            "directions_url": _directions_url(business.latitude, business.longitude, business.address),
            "bot_username": bot_username,
        },
        "hours": layout_cfg.get("hours"),
    }


async def _build_payload(slug: str, db: AsyncSession) -> Optional[dict]:
    business = (await db.execute(
        select(Business).where(
            Business.slug == slug,
            Business.deleted_at.is_(None),
            Business.is_suspended.is_(False),   # suspended → public store/site goes dark
        )
    )).scalar_one_or_none()
    if business is None:
        return None

    cfg = (await db.execute(
        select(MiniAppConfig).where(MiniAppConfig.business_id == business.id)
    )).scalar_one_or_none()
    bot = (await db.execute(
        select(Bot)
        .where(Bot.business_id == business.id, Bot.status == BotStatus.active)
        .order_by(Bot.created_at.asc()).limit(1)
    )).scalar_one_or_none()
    items = list((await db.execute(
        select(KnowledgeItem)
        .where(KnowledgeItem.business_id == business.id, KnowledgeItem.is_active.is_(True))
        .order_by(KnowledgeItem.created_at.desc())
    )).scalars().all())

    return {
        "business": {
            "slug": business.slug,
            "name": business.name,
            "tagline": (cfg.layout_config or {}).get("tagline") if cfg and cfg.layout_config else business.description,
            "category": business.category,
            "logo_url": business.logo_url,
            "phone": business.phone,
            "address": business.address,
            "currency": business.currency,
            "bot_username": bot.bot_username if bot else None,
        },
        "theme": _theme(cfg, business),
        "layout": {"sections": _sections(cfg)},
        "content": _content(business, bot, cfg, items),
    }


# ── cache ────────────────────────────────────────────────────────────────────

async def bust_storefront_cache(business_id: uuid.UUID, db: AsyncSession, redis) -> None:
    """Drop a business's cached storefront AND landing page (incl. any custom
    domain) so the next request rebuilds them. Called from catalog/config edits."""
    slug = await db.scalar(select(Business.slug).where(Business.id == business_id))
    if slug:
        await redis.delete(_CACHE_KEY.format(slug=slug))
        await redis.delete(f"landing:html:{slug}")
    from app.db.models import CustomDomain
    domains = (await db.execute(
        select(CustomDomain.domain).where(CustomDomain.business_id == business_id)
    )).scalars().all()
    for d in domains:
        await redis.delete(f"landing:host:{d}")


# ── endpoint ─────────────────────────────────────────────────────────────────

@router.get("/{slug}")
async def get_storefront(
    slug: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> dict:
    key = _CACHE_KEY.format(slug=slug)
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)

    payload = await _build_payload(slug, db)
    if payload is None:
        raise NotFoundError("Storefront", slug)

    await redis.set(key, json.dumps(payload), ex=_CACHE_TTL)
    return payload


# ── public website support chat ──────────────────────────────────────────────

# Abuse/cost guards for the UNAUTHENTICATED website chat. Per-IP throttle stops
# a single visitor flooding; per-business daily cap bounds total LLM cost (over
# the cap we gracefully hand off to Telegram instead of erroring).
_WEBCHAT_IP_PER_MIN = 8
_WEBCHAT_BIZ_PER_DAY = 300


class WebChatTurn(BaseModel):
    role: str
    content: str


class WebChatRequest(BaseModel):
    message: str
    history: list[WebChatTurn] = []


class WebChatResponse(BaseModel):
    reply: str
    handoff: bool = False    # True → suggest continuing on Telegram (cap reached)


async def _webchat_ip_throttle(redis, ip: str) -> None:
    key = f"webchat:ip:{ip}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > _WEBCHAT_IP_PER_MIN:
        raise RateLimitError(retry_after=60)


async def _webchat_biz_over_cap(redis, business_id) -> bool:
    key = f"webchat:biz:{business_id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 86400)
    return count > _WEBCHAT_BIZ_PER_DAY


@router.post("/{slug}/chat", response_model=WebChatResponse)
async def website_chat(
    slug: str,
    body: WebChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> WebChatResponse:
    """Public, stateless AI support chat for the landing page. Rate-limited per
    IP and per business; replies are metered as a zero-rated UsageEvent."""
    ip = request.client.host if request.client else "unknown"
    await _webchat_ip_throttle(redis, ip)

    business = (await db.execute(
        select(Business).where(
            Business.slug == slug,
            Business.deleted_at.is_(None),
            Business.is_suspended.is_(False),
        )
    )).scalar_one_or_none()
    if business is None:
        raise NotFoundError("Storefront", slug)

    if await _webchat_biz_over_cap(redis, business.id):
        return WebChatResponse(
            reply="Our web assistant is taking a quick break — please continue the "
                  "chat on Telegram and we'll help you right away.",
            handoff=True)

    from app.services import web_chat_service
    result = await web_chat_service.answer(
        db, business, body.message, [t.model_dump() for t in body.history])

    # Meter the reply (zero-rated for now — the rate limits are the cost guard).
    if result.get("source") == "ai":
        db.add(UsageEvent(
            business_id=business.id, action_type="web_chat",
            model_id=result.get("model"), input_tokens=0, output_tokens=0,
            etg_charged=0, payer="business"))
        await db.flush()

    return WebChatResponse(reply=result["reply"])
