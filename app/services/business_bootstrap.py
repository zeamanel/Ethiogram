# app/services/business_bootstrap.py
"""Instant activation: one prompt → a working business.

The owner describes their business in a sentence or three; ONE LLM call
generates everything the platform needs to feel alive in minute one:

  • about / tagline / CTA / hours  → Business + storefront copy
  • theme (colours + fonts)        → MiniAppConfig
  • products / services / FAQs     → KnowledgeItems (the bot answers from these)
  • SEO metadata                   → LandingPage

Everything is sanitised the same way the storefront generator is, and the
whole thing is fail-safe: bad LLM output writes nothing rather than garbage.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import KnowledgeItem, KnowledgeItemType, LandingPage, MiniAppConfig
from app.services.model_router import model_router
from app.services.storefront_ai import (
    SUPPORTED_FONTS,
    _extract_json,
    _sanitize_content,
    _sanitize_seo,
    _sanitize_theme,
)

logger = get_logger(__name__)

_MAX_ITEMS = 12
_MAX_FAQS = 8

_BOOTSTRAP_SYS = (
    "You are a one-shot business-setup assistant for an Ethiopian SMB platform. "
    "From the owner's description, build their complete online presence. Write "
    "all customer-facing text in the language the owner wrote in (Amharic stays "
    "Amharic). Respond with ONLY a compact JSON object (no prose, no markdown) "
    "with keys:\n"
    "about (1-2 warm sentences, <= 240 chars);\n"
    "tagline (<= 80 chars, punchy);\n"
    "cta (action phrase <= 24 chars);\n"
    "hours (opening-hours line, or empty string if not mentioned);\n"
    "theme: object with primary, accent, bg, text, surface (6-digit hex, strong "
    "text/bg contrast) and font_heading + font_body (each EXACTLY one of: "
    + ", ".join(SUPPORTED_FONTS) + ");\n"
    "products: array (max 12) of {name, price, description} — ONLY items the "
    "owner actually mentioned or clearly implied; price as a plain string like "
    '"250 ETB" or "" if unknown;\n'
    "services: array (max 12) of {name, price, duration, description} — "
    'duration as free text like "30 minutes" or "1 hour";\n'
    "faqs: array (max 8) of {question, answer} customers would genuinely ask "
    "(opening hours, location, payment, delivery, booking);\n"
    "seo: object with title (<= 60 chars), meta_description (<= 155), "
    "hero_headline (<= 70), hero_subheadline (<= 120), keywords (5-8 search "
    "phrases). Never invent specific facts (addresses, phone numbers) the "
    "owner didn't give — leave them out instead."
)


def _clean_str(v, cap: int) -> str:
    return str(v).strip()[:cap] if isinstance(v, (str, int, float)) and str(v).strip() else ""


def _sanitize_catalog(raw, *, with_duration: bool) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw[:_MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        name = _clean_str(entry.get("name"), 120)
        if not name:
            continue
        item = {"name": name,
                "price": _clean_str(entry.get("price"), 32),
                "description": _clean_str(entry.get("description"), 300)}
        if with_duration:
            item["duration"] = _clean_str(entry.get("duration"), 40)
        out.append(item)
    return out


def _sanitize_faqs(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw[:_MAX_FAQS]:
        if not isinstance(entry, dict):
            continue
        q = _clean_str(entry.get("question"), 200)
        a = _clean_str(entry.get("answer"), 500)
        if q and a:
            out.append({"question": q, "answer": a})
    return out


async def bootstrap(db: AsyncSession, business, brief: str) -> dict:
    """Generate + persist the full setup. Returns a summary dict.
    Raises nothing: an unusable LLM response returns {"source": "fallback"}
    with zero writes so the owner can just try again."""
    ctx = f"Business name: {business.name}"
    if getattr(business, "category", None):
        ctx += f"\nCategory: {business.category}"
    ctx += f"\n\nOwner's description (follow closely):\n{brief.strip()}"

    raw, model_used = {}, None
    try:
        text, _tokens, model_used = await model_router.execute_with_fallback(
            messages=[{"role": "user", "content": f"{ctx}\n\nBuild the JSON now."}],
            system_prompt=_BOOTSTRAP_SYS,
            business_id=business.id,
            max_tokens=2200,
            temperature=0.7,
        )
        raw = _extract_json(text)
    except Exception as exc:
        logger.warning("Bootstrap LLM call failed", business_id=str(business.id),
                       error=f"{type(exc).__name__}: {exc}")

    if not raw:
        return {"source": "fallback", "model": model_used,
                "products": 0, "services": 0, "faqs": 0}

    content = _sanitize_content(raw)                       # tagline/about/cta/hours
    theme = _sanitize_theme(raw.get("theme") if isinstance(raw.get("theme"), dict) else {})
    products = _sanitize_catalog(raw.get("products"), with_duration=False)
    services = _sanitize_catalog(raw.get("services"), with_duration=True)
    faqs = _sanitize_faqs(raw.get("faqs"))
    seo = _sanitize_seo(raw.get("seo") if isinstance(raw.get("seo"), dict) else {})

    # ── write: business copy ────────────────────────────────────────────────
    if content.get("about"):
        business.description = content["about"]

    # ── write: storefront config (upsert, publish) ──────────────────────────
    cfg = (await db.execute(
        select(MiniAppConfig).where(MiniAppConfig.business_id == business.id)
    )).scalar_one_or_none()
    if cfg is None:
        cfg = MiniAppConfig(business_id=business.id)
        db.add(cfg)
        await db.flush()
    layout = dict(cfg.layout_config or {})
    if content.get("tagline"):
        layout["tagline"] = content["tagline"]
    if content.get("cta"):
        layout["hero_cta"] = content["cta"]
    if content.get("hours"):
        layout["hours"] = content["hours"]
    if theme:
        cfg.theme_primary = theme["primary"]
        cfg.theme_accent = theme["accent"]
        cfg.font_heading = theme["font_heading"]
        cfg.font_body = theme["font_body"]
        overrides = dict(layout.get("theme_overrides") or {})
        overrides.update({"bg": theme["bg"], "text": theme["text"], "surface": theme["surface"]})
        layout["theme_overrides"] = overrides
    cfg.layout_config = layout
    cfg.ui_child_prompt = brief.strip()[:2000]     # becomes the reusable AI brief
    if not cfg.is_published:
        cfg.is_published = True
        cfg.published_at = datetime.now(timezone.utc)

    # ── write: catalog + FAQs (skip titles that already exist) ──────────────
    existing_titles = {
        (t or "").strip().lower()
        for t in (await db.execute(
            select(KnowledgeItem.title).where(KnowledgeItem.business_id == business.id)
        )).scalars().all()
    }

    def _add_items(entries, item_type, data_keys):
        added = 0
        for e in entries:
            if e["name"].strip().lower() in existing_titles:
                continue
            data = {k: e[k] for k in data_keys if e.get(k)}
            db.add(KnowledgeItem(
                business_id=business.id, item_type=item_type, title=e["name"],
                body=e.get("description") or None, data=data or None, is_active=True))
            existing_titles.add(e["name"].strip().lower())
            added += 1
        return added

    n_products = _add_items(products, KnowledgeItemType.product, ("price",))
    n_services = _add_items(services, KnowledgeItemType.service, ("price", "duration"))
    n_faqs = 0
    for f in faqs:
        if f["question"].strip().lower() in existing_titles:
            continue
        db.add(KnowledgeItem(
            business_id=business.id, item_type=KnowledgeItemType.faq,
            title=f["question"], body=f["answer"], is_active=True))
        existing_titles.add(f["question"].strip().lower())
        n_faqs += 1

    # ── write: website SEO (upsert, publish) ─────────────────────────────────
    if seo:
        lp = (await db.execute(
            select(LandingPage).where(LandingPage.business_id == business.id)
        )).scalar_one_or_none()
        if lp is None:
            lp = LandingPage(business_id=business.id)
            db.add(lp)
        if seo.get("title"):
            lp.title = seo["title"]
        if seo.get("meta_description"):
            lp.meta_description = seo["meta_description"]
        if seo.get("hero_headline"):
            lp.hero_headline = seo["hero_headline"]
        if seo.get("hero_subheadline"):
            lp.hero_subheadline = seo["hero_subheadline"]
        if seo.get("keywords"):
            lp.seo_keywords = seo["keywords"]
        lp.is_published = True

    await db.flush()
    logger.info("Business bootstrapped from brief", business_id=str(business.id),
                products=n_products, services=n_services, faqs=n_faqs,
                theme=bool(theme), seo=bool(seo))
    return {"source": "ai", "model": model_used,
            "products": n_products, "services": n_services, "faqs": n_faqs}
