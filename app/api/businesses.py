# app/api/businesses.py
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import Business, BusinessBrainConfig, Conversation, LandingPage, MiniAppConfig
from app.db.session import get_db, get_redis
from app.utils.text import slugify

logger = get_logger(__name__)

router = APIRouter(prefix="/businesses", tags=["businesses"])


class CreateBusinessRequest(BaseModel):
    name: str
    category: Optional[str] = None
    description: Optional[str] = None
    country_code: Optional[str] = None     # falls back to model default (ET)
    timezone: Optional[str] = None         # falls back to model default (Africa/Addis_Ababa)
    currency: Optional[str] = None         # falls back to model default (ETB)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 2:
            raise ValueError("Business name must be at least 2 characters")
        if len(v) > 255:
            raise ValueError("Business name too long")
        return v


class BusinessResponse(BaseModel):
    id: str
    name: str
    slug: str
    category: Optional[str]


async def _unique_slug(name: str, db: AsyncSession) -> str:
    base = slugify(name) or "business"
    slug, n = base, 1
    while await db.scalar(select(Business.id).where(Business.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    return slug


@router.post("", response_model=BusinessResponse, status_code=201)
async def create_business(
    body: CreateBusinessRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BusinessResponse:
    """
    Create a business owned by the current user (onboarding step 1). The wallet,
    brain config, and ETG signup bonus are created when the owner connects their
    first bot via POST /bots.
    """
    fields = {"owner_id": current_user.id, "name": body.name, "slug": await _unique_slug(body.name, db)}
    if body.category:      fields["category"] = body.category
    if body.description:   fields["description"] = body.description
    if body.country_code:  fields["country_code"] = body.country_code
    if body.timezone:      fields["timezone"] = body.timezone
    if body.currency:      fields["currency"] = body.currency

    business = Business(**fields)
    db.add(business)
    await db.flush()
    logger.info("Business created", business_id=str(business.id),
                owner_id=str(current_user.id), slug=business.slug)
    return BusinessResponse(
        id=str(business.id), name=business.name, slug=business.slug, category=business.category,
    )


# ---------------------------------------------------------------------------
# Business Brain settings (persona, tone, fallback, RAG tuning)
# ---------------------------------------------------------------------------

class BrainConfigResponse(BaseModel):
    persona_name: str
    persona_tone: str
    system_prompt_extra: Optional[str]
    fallback_message: str
    handoff_message: str
    out_of_hours_message: Optional[str]
    rag_top_k: int
    rag_similarity_threshold: float
    max_history_messages: int
    collect_customer_name: bool
    collect_customer_phone: bool


class BrainConfigUpdate(BaseModel):
    persona_name: Optional[str] = None
    persona_tone: Optional[str] = None
    system_prompt_extra: Optional[str] = None
    fallback_message: Optional[str] = None
    handoff_message: Optional[str] = None
    out_of_hours_message: Optional[str] = None
    rag_top_k: Optional[int] = None
    rag_similarity_threshold: Optional[float] = None
    max_history_messages: Optional[int] = None
    collect_customer_name: Optional[bool] = None
    collect_customer_phone: Optional[bool] = None

    @field_validator("rag_similarity_threshold")
    @classmethod
    def _thresh(cls, v):
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("rag_similarity_threshold must be between 0.0 and 1.0")
        return v

    @field_validator("rag_top_k")
    @classmethod
    def _topk(cls, v):
        if v is not None and not (1 <= v <= 20):
            raise ValueError("rag_top_k must be between 1 and 20")
        return v

    @field_validator("max_history_messages")
    @classmethod
    def _hist(cls, v):
        if v is not None and not (1 <= v <= 100):
            raise ValueError("max_history_messages must be between 1 and 100")
        return v

    @field_validator("persona_name")
    @classmethod
    def _persona(cls, v):
        if v is not None:
            v = v.strip()
            if not (1 <= len(v) <= 128):
                raise ValueError("persona_name must be 1-128 characters")
        return v


async def _get_owned_business(business_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Business:
    # Suspended / deleted businesses are not accessible to their owner.
    result = await db.execute(
        select(Business).where(
            Business.id == business_id, Business.owner_id == user_id,
            Business.is_suspended.is_(False), Business.deleted_at.is_(None),
        )
    )
    business = result.scalar_one_or_none()
    if business is None:
        raise NotFoundError("Business", str(business_id))
    return business


def _brain_to_response(c: BusinessBrainConfig) -> BrainConfigResponse:
    # A non-persisted config (defaults path) has None attrs until flush, so fall
    # back to each column's declared default.
    cols = BusinessBrainConfig.__table__.c

    def val(name):
        v = getattr(c, name)
        if v is None and cols[name].default is not None:
            return cols[name].default.arg
        return v

    return BrainConfigResponse(
        persona_name=val("persona_name"), persona_tone=val("persona_tone"),
        system_prompt_extra=val("system_prompt_extra"), fallback_message=val("fallback_message"),
        handoff_message=val("handoff_message"), out_of_hours_message=val("out_of_hours_message"),
        rag_top_k=val("rag_top_k"), rag_similarity_threshold=val("rag_similarity_threshold"),
        max_history_messages=val("max_history_messages"),
        collect_customer_name=val("collect_customer_name"),
        collect_customer_phone=val("collect_customer_phone"),
    )


@router.get("/{business_id}/brain", response_model=BrainConfigResponse)
async def get_brain_config(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BrainConfigResponse:
    await _get_owned_business(business_id, current_user.id, db)
    config = (await db.execute(
        select(BusinessBrainConfig).where(BusinessBrainConfig.business_id == business_id)
    )).scalar_one_or_none()
    if config is None:
        # No config yet (business without a bot) — return model defaults to edit.
        config = BusinessBrainConfig(business_id=business_id)
    return _brain_to_response(config)


@router.patch("/{business_id}/brain", response_model=BrainConfigResponse)
async def update_brain_config(
    business_id: uuid.UUID,
    body: BrainConfigUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BrainConfigResponse:
    """Edit the Business Brain (persona, tone, fallback, RAG tuning). Upserts the
    config if the business doesn't have one yet (e.g. no bot connected)."""
    await _get_owned_business(business_id, current_user.id, db)
    config = (await db.execute(
        select(BusinessBrainConfig).where(BusinessBrainConfig.business_id == business_id)
    )).scalar_one_or_none()

    changes = body.model_dump(exclude_unset=True)
    if config is None:
        config = BusinessBrainConfig(business_id=business_id, **changes)
        db.add(config)
        await db.flush()
    else:
        for k, v in changes.items():
            setattr(config, k, v)

    logger.info("Brain config updated", business_id=str(business_id), fields=list(changes.keys()))
    return _brain_to_response(config)


# ---------------------------------------------------------------------------
# Storefront (customer Mini App) config — theme, sections, publish state
# ---------------------------------------------------------------------------

# Every section the storefront can render, with an owner-facing label.
_SECTION_LABELS = {
    "hero": "Hero banner",
    "categories": "Category chips",
    "products": "Products grid",
    "services": "Services list",
    "menu": "Menu",
    "hours": "Opening hours",
    "contact": "Contact info",
    "chat": "AI chat widget",
}
# Theme keys stored in layout_config.theme_overrides (the rest are columns).
_OVERRIDE_KEYS = ("bg", "text", "surface", "border", "radius", "font_heading")
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}")


class StorefrontTheme(BaseModel):
    primary: Optional[str] = None
    secondary: Optional[str] = None
    accent: Optional[str] = None
    bg: Optional[str] = None
    text: Optional[str] = None
    surface: Optional[str] = None
    border: Optional[str] = None         # may be rgba() — not hex-validated
    radius: Optional[str] = None
    font_heading: Optional[str] = None
    font_body: Optional[str] = None

    @field_validator("primary", "secondary", "accent", "bg", "text", "surface")
    @classmethod
    def _hex(cls, v):
        if v is not None and not _HEX_RE.fullmatch(v):
            raise ValueError("must be a hex color like #E8421A")
        return v


class StorefrontSection(BaseModel):
    type: str
    visible: bool = True
    order: int = 0


class StorefrontResponse(BaseModel):
    theme: dict
    tagline: Optional[str]
    hours: Optional[str]
    logo_url: Optional[str]
    sections: list[dict]          # [{type, label, visible, order}] — ALL known types
    is_published: bool
    store_url: str


class StorefrontUpdate(BaseModel):
    theme: Optional[StorefrontTheme] = None
    tagline: Optional[str] = None
    hours: Optional[str] = None
    logo_url: Optional[str] = None
    sections: Optional[list[StorefrontSection]] = None
    is_published: Optional[bool] = None


def _storefront_to_response(cfg: Optional[MiniAppConfig], business: Business) -> StorefrontResponse:
    # Reuse the storefront's OWN assembly so the editor shows exactly what renders.
    from app.api.miniapp import _theme, _DEFAULT_SECTIONS
    layout = (cfg.layout_config or {}) if cfg else {}
    stored = layout.get("sections") if isinstance(layout.get("sections"), list) else _DEFAULT_SECTIONS

    sections, seen, order = [], [], 0
    for s in stored:
        t = s.get("type")
        if t in _SECTION_LABELS and t not in seen:
            sections.append({"type": t, "label": _SECTION_LABELS[t],
                             "visible": s.get("visible", True), "order": order})
            seen.append(t); order += 1
    for t in _SECTION_LABELS:           # surface any known type not yet configured
        if t not in seen:
            sections.append({"type": t, "label": _SECTION_LABELS[t], "visible": False, "order": order})
            seen.append(t); order += 1

    return StorefrontResponse(
        theme=_theme(cfg, business),
        tagline=layout.get("tagline") or business.description,
        hours=layout.get("hours"),
        logo_url=business.logo_url,
        sections=sections,
        is_published=bool(cfg.is_published) if cfg else False,
        store_url=f"/app/store/?s={business.slug}",
    )


@router.get("/{business_id}/storefront", response_model=StorefrontResponse)
async def get_storefront_config(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> StorefrontResponse:
    business = await _get_owned_business(business_id, current_user.id, db)
    cfg = (await db.execute(
        select(MiniAppConfig).where(MiniAppConfig.business_id == business_id)
    )).scalar_one_or_none()
    return _storefront_to_response(cfg, business)


@router.patch("/{business_id}/storefront", response_model=StorefrontResponse)
async def update_storefront_config(
    business_id: uuid.UUID,
    body: StorefrontUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> StorefrontResponse:
    """Edit the customer storefront (theme, sections, tagline, hours, publish).
    Upserts the config; busts the public storefront cache so changes show live."""
    business = await _get_owned_business(business_id, current_user.id, db)
    cfg = (await db.execute(
        select(MiniAppConfig).where(MiniAppConfig.business_id == business_id)
    )).scalar_one_or_none()
    if cfg is None:
        cfg = MiniAppConfig(business_id=business_id)
        db.add(cfg)
        await db.flush()

    layout = dict(cfg.layout_config or {})   # copy → reassign so JSONB change is detected

    if body.theme is not None:
        t = body.theme
        if t.primary:   cfg.theme_primary = t.primary
        if t.secondary: cfg.theme_secondary = t.secondary
        if t.accent:    cfg.theme_accent = t.accent
        if t.font_body: cfg.font_family = t.font_body
        overrides = dict(layout.get("theme_overrides") or {})
        for k in _OVERRIDE_KEYS:
            v = getattr(t, k)
            if v:
                overrides[k] = v
        if overrides:
            layout["theme_overrides"] = overrides

    if body.tagline is not None:
        layout["tagline"] = body.tagline.strip() or None
    if body.hours is not None:
        layout["hours"] = body.hours.strip() or None
    if body.logo_url is not None:
        business.logo_url = body.logo_url.strip() or None   # lives on Business, not the JSONB
    if body.sections is not None:
        layout["sections"] = [
            {"type": s.type, "visible": s.visible, "order": i}
            for i, s in enumerate(body.sections) if s.type in _SECTION_LABELS
        ]

    cfg.layout_config = layout

    if body.is_published is not None:
        cfg.is_published = body.is_published
        if body.is_published and cfg.published_at is None:
            cfg.published_at = datetime.now(timezone.utc)

    from app.api.miniapp import bust_storefront_cache
    await bust_storefront_cache(business_id, db, redis)
    logger.info("Storefront config updated", business_id=str(business_id))
    return _storefront_to_response(cfg, business)


# ---------------------------------------------------------------------------
# Website (SEO landing page) config — title, meta, hero, keywords, publish
# ---------------------------------------------------------------------------

class WebsiteResponse(BaseModel):
    title: Optional[str]
    meta_description: Optional[str]
    hero_headline: Optional[str]
    hero_subheadline: Optional[str]
    seo_keywords: list[str]
    og_image_url: Optional[str]
    is_published: bool
    website_url: str


class WebsiteUpdate(BaseModel):
    title: Optional[str] = None
    meta_description: Optional[str] = None
    hero_headline: Optional[str] = None
    hero_subheadline: Optional[str] = None
    seo_keywords: Optional[list[str]] = None
    og_image_url: Optional[str] = None
    is_published: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def _title_len(cls, v):
        if v is not None and len(v) > 70:
            raise ValueError("SEO title should be 70 characters or fewer")
        return v

    @field_validator("meta_description")
    @classmethod
    def _meta_len(cls, v):
        if v is not None and len(v) > 160:
            raise ValueError("Meta description should be 160 characters or fewer")
        return v


def _website_to_response(lp: Optional[LandingPage], business: Business) -> WebsiteResponse:
    return WebsiteResponse(
        title=lp.title if lp else None,
        meta_description=lp.meta_description if lp else None,
        hero_headline=lp.hero_headline if lp else None,
        hero_subheadline=lp.hero_subheadline if lp else None,
        seo_keywords=(lp.seo_keywords or []) if lp else [],
        og_image_url=lp.og_image_url if lp else None,
        is_published=bool(lp.is_published) if lp else False,
        website_url=f"/biz/{business.slug}",
    )


@router.get("/{business_id}/website", response_model=WebsiteResponse)
async def get_website_config(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> WebsiteResponse:
    business = await _get_owned_business(business_id, current_user.id, db)
    lp = (await db.execute(
        select(LandingPage).where(LandingPage.business_id == business_id)
    )).scalar_one_or_none()
    return _website_to_response(lp, business)


@router.patch("/{business_id}/website", response_model=WebsiteResponse)
async def update_website_config(
    business_id: uuid.UUID,
    body: WebsiteUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> WebsiteResponse:
    """Edit the SEO landing page (title, meta, hero, keywords, OG image, publish).
    Upserts the LandingPage; busts the public page cache so changes show live."""
    business = await _get_owned_business(business_id, current_user.id, db)
    lp = (await db.execute(
        select(LandingPage).where(LandingPage.business_id == business_id)
    )).scalar_one_or_none()
    if lp is None:
        lp = LandingPage(business_id=business_id)
        db.add(lp)
        await db.flush()

    changes = body.model_dump(exclude_unset=True)
    publish = changes.pop("is_published", None)
    for k, v in changes.items():
        # blank strings clear back to NULL (page falls back to computed defaults)
        setattr(lp, k, (v.strip() or None) if isinstance(v, str) else v)
    if publish is not None:
        lp.is_published = publish
        if publish and lp.published_at is None:
            lp.published_at = datetime.now(timezone.utc)

    from app.api.miniapp import bust_storefront_cache
    await bust_storefront_cache(business_id, db, redis)
    logger.info("Website config updated", business_id=str(business_id), fields=list(changes.keys()))
    return _website_to_response(lp, business)


# ---------------------------------------------------------------------------
# Billing settings — who pays per message, per-user cap, user-pays pricing
# ---------------------------------------------------------------------------

_BILLING_POLICIES = {"business_pays", "user_pays", "both"}
_LIMIT_ACTIONS = {"block", "user_pays"}


class BillingResponse(BaseModel):
    billing_policy: str
    per_user_monthly_limit: Optional[int]
    per_user_limit_action: str
    service_price: int
    business_markup: int


class BillingUpdate(BaseModel):
    billing_policy: Optional[str] = None
    per_user_monthly_limit: Optional[int] = None
    per_user_limit_action: Optional[str] = None
    service_price: Optional[int] = None
    business_markup: Optional[int] = None

    @field_validator("billing_policy")
    @classmethod
    def _policy(cls, v):
        if v is not None and v not in _BILLING_POLICIES:
            raise ValueError(f"billing_policy must be one of {sorted(_BILLING_POLICIES)}")
        return v

    @field_validator("per_user_limit_action")
    @classmethod
    def _action(cls, v):
        if v is not None and v not in _LIMIT_ACTIONS:
            raise ValueError(f"per_user_limit_action must be one of {sorted(_LIMIT_ACTIONS)}")
        return v

    @field_validator("per_user_monthly_limit")
    @classmethod
    def _limit(cls, v):
        if v is not None and v < 0:
            raise ValueError("per_user_monthly_limit must be >= 0")
        return v

    @field_validator("service_price", "business_markup")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be >= 0")
        return v


def _billing_to_response(b: Business) -> BillingResponse:
    return BillingResponse(
        billing_policy=b.billing_policy,
        per_user_monthly_limit=b.per_user_monthly_limit,
        per_user_limit_action=b.per_user_limit_action,
        service_price=b.service_price,
        business_markup=b.business_markup,
    )


@router.get("/{business_id}/billing", response_model=BillingResponse)
async def get_billing(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BillingResponse:
    business = await _get_owned_business(business_id, current_user.id, db)
    return _billing_to_response(business)


@router.patch("/{business_id}/billing", response_model=BillingResponse)
async def update_billing(
    business_id: uuid.UUID,
    body: BillingUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BillingResponse:
    """Edit who pays per message, the per-user monthly cap + action, and the
    user-pays service price / business markup."""
    business = await _get_owned_business(business_id, current_user.id, db)
    changes = body.model_dump(exclude_unset=True)   # None for per_user_monthly_limit clears it
    for k, v in changes.items():
        setattr(business, k, v)
    logger.info("Billing updated", business_id=str(business_id), fields=list(changes.keys()))
    return _billing_to_response(business)


# ── per-user monthly usage ────────────────────────────────────────────────────

class UserUsageRow(BaseModel):
    conversation_id: str
    customer_id: str
    customer_name: Optional[str]
    customer_username: Optional[str]
    monthly_etg_used: int
    etg_balance: int
    total_etg_spent: int
    last_message_at: Optional[str]


class UserUsageList(BaseModel):
    items: list[UserUsageRow]
    total: int
    limit: int
    offset: int


@router.get("/{business_id}/users/usage", response_model=UserUsageList)
async def list_user_usage(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    search: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> UserUsageList:
    """Per-end-user monthly ETG usage for the business (one row per customer
    conversation), newest spenders first. Supports search + pagination."""
    await _get_owned_business(business_id, current_user.id, db)
    from sqlalchemy import func as _func
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    stmt = select(Conversation).where(Conversation.business_id == business_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            Conversation.customer_name.ilike(like)
            | Conversation.customer_username.ilike(like)
            | Conversation.customer_platform_id.ilike(like)
        )
    total = await db.scalar(select(_func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(
        stmt.order_by(Conversation.monthly_etg_used.desc()).limit(limit).offset(offset)
    )).scalars().all()

    items = [UserUsageRow(
        conversation_id=str(c.id),
        customer_id=c.customer_platform_id,
        customer_name=c.customer_name,
        customer_username=c.customer_username,
        monthly_etg_used=c.monthly_etg_used or 0,
        etg_balance=c.etg_balance or 0,
        total_etg_spent=c.total_etg_spent or 0,
        last_message_at=c.last_message_at.isoformat() if c.last_message_at else None,
    ) for c in rows]
    return UserUsageList(items=items, total=total, limit=limit, offset=offset)
