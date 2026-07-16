# app/services/storefront_ai.py
"""LLM-assisted storefront generation.

Two one-shot helpers behind the editor's "Generate page" (theme) and "Generate
content" (copy) buttons. Both build a prompt from the business's OWN data plus
the owner's "store vibe", call the shared model router, and return SANITISED
suggestions for the editor to preview — nothing is saved here. Every path is
fail-safe: a bad/empty/oversized LLM response falls back to deterministic
defaults so the buttons never error in the owner's face.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import KnowledgeItem, KnowledgeItemType
from app.services.model_router import model_router

logger = get_logger(__name__)

# Fonts the storefront actually ships (must match the editor's <select> options).
SUPPORTED_FONTS = ("Sora", "Inter", "Poppins", "DM Sans", "Fraunces", "Syne")
# Every section type the storefront can render (must match businesses._SECTION_LABELS).
SECTION_TYPES = ("hero", "categories", "products", "services", "menu",
                 "hours", "contact", "chat")
_HEX6 = re.compile(r"#[0-9A-Fa-f]{6}")
_THEME_COLOR_KEYS = ("primary", "accent", "bg", "text", "surface")

_THEME_SYS = (
    "You are a senior brand designer for small businesses. Design a tasteful, "
    "accessible storefront colour theme and font pairing for the business. "
    "Respond with ONLY a compact JSON object (no prose, no markdown) with keys: "
    'primary, accent, bg, text, surface (all 6-digit hex like "#1A73E8"), '
    "font_heading and font_body (each EXACTLY one of: " + ", ".join(SUPPORTED_FONTS) + "). "
    "Ensure strong contrast between text and bg."
)
_CONTENT_SYS = (
    "You are a marketing copywriter for small businesses. Write storefront copy "
    "in the business's own language. Respond with ONLY a compact JSON object "
    "(no prose, no markdown) with keys: "
    "tagline (<= 80 characters, punchy headline), "
    "about (1-2 warm sentences describing the business, <= 240 characters), "
    "cta (the storefront button text, an action phrase <= 24 characters, "
    'e.g. "Book your visit", "Get a quote", "Shop now"), and '
    "hours (a short plausible opening-hours line, or empty string if unknown)."
)
_FULL_SYS = (
    "You are a one-shot website builder for small businesses: brand designer, "
    "copywriter and information architect in one. The owner has written a brief "
    "describing the page they want — FOLLOW THE BRIEF CLOSELY; it outranks every "
    "other signal. Design their complete storefront page. Respond with ONLY a "
    "compact JSON object (no prose, no markdown) with keys:\n"
    "theme: object with primary, accent, bg, text, surface (6-digit hex like "
    '"#1A73E8", strong text/bg contrast), font_heading and font_body (each '
    "EXACTLY one of: " + ", ".join(SUPPORTED_FONTS) + ");\n"
    "tagline (<= 80 chars, punchy, in the business's own language);\n"
    "about (1-2 warm sentences, <= 240 chars);\n"
    "cta (action phrase <= 24 chars);\n"
    "hours (short opening-hours line, or empty string if unknown);\n"
    "sections: array of section types to SHOW, in display order, chosen only "
    "from: " + ", ".join(SECTION_TYPES) + ". Pick what fits the business — e.g. "
    'a restaurant shows "menu", a salon shows "services", a shop shows '
    '"products"; always start with "hero"; include "chat" so customers can ask '
    "questions; skip types with nothing to show."
)


def _extract_json(text: str | None) -> dict:
    """Best-effort: parse the first {...} block out of an LLM response."""
    if not text:
        return {}
    try:
        start = text.index("{")
        end = text.rindex("}")
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


async def _catalog_snippet(db: AsyncSession, business_id) -> tuple[list[str], list[str]]:
    rows = (await db.execute(
        select(KnowledgeItem.title, KnowledgeItem.item_type).where(
            KnowledgeItem.business_id == business_id,
            KnowledgeItem.is_active.is_(True),
        ).limit(12)
    )).all()
    products = [t for (t, k) in rows if k == KnowledgeItemType.product and t]
    services = [t for (t, k) in rows if k == KnowledgeItemType.service and t]
    return products, services


def _context(business, vibe, products, services) -> str:
    parts = [f"Business name: {business.name}"]
    if getattr(business, "category", None):
        parts.append(f"Category: {business.category}")
    if getattr(business, "description", None):
        parts.append(f"About: {business.description}")
    if products:
        parts.append("Products: " + ", ".join(products[:8]))
    if services:
        parts.append("Services: " + ", ".join(services[:8]))
    if vibe:
        parts.append(f"Owner's brief (follow closely): {vibe}")
    return "\n".join(parts)


def _fallback_theme() -> dict:
    return {
        "primary": "#1a73e8", "accent": "#fbbc04", "bg": "#ffffff",
        "text": "#14110d", "surface": "#f7f5f0",
        "font_heading": "Sora", "font_body": "Inter",
    }


def _sanitize_theme(raw: dict) -> dict:
    """Keep only valid hex colours + supported fonts; require the core trio."""
    out: dict = {}
    for k in _THEME_COLOR_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and _HEX6.fullmatch(v.strip()):
            out[k] = v.strip().lower()
    if raw.get("font_heading") in SUPPORTED_FONTS:
        out["font_heading"] = raw["font_heading"]
    if raw.get("font_body") in SUPPORTED_FONTS:
        out["font_body"] = raw["font_body"]
    if not all(k in out for k in ("primary", "bg", "text")):
        return {}                       # too little to be useful → caller falls back
    for k, v in _fallback_theme().items():
        out.setdefault(k, v)            # fill any missing optional keys
    return out


def _sanitize_content(raw: dict) -> dict:
    out: dict = {}
    for key, cap in (("tagline", 120), ("about", 400), ("cta", 32), ("hours", 120)):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()[:cap]
    return out


def _sanitize_sections(raw) -> list[str]:
    """Filter the LLM's section list to known types, deduped, order preserved.
    Hero is forced first — a storefront without a hero renders headless.
    Returns [] when nothing valid came back (caller keeps current layout)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for s in raw:
        t = s.get("type") if isinstance(s, dict) else s
        if isinstance(t, str) and t.strip().lower() in SECTION_TYPES:
            t = t.strip().lower()
            if t not in out:
                out.append(t)
    if not out:
        return []
    if "hero" in out:
        out.remove("hero")
    return ["hero"] + out


async def generate(db: AsyncSession, business, kind: str, vibe: str | None = None) -> dict:
    """Generate a storefront theme ("page"), copy ("content"), or the whole
    page at once ("full": theme + copy + section layout from the owner's brief).

    Returns ``{kind, source, theme?, tagline?, hours?, sections?}`` where source
    is "ai" when the LLM produced usable output and "fallback" otherwise.
    Never raises.
    """
    if kind not in ("content", "page", "full"):
        kind = "page"
    products, services = await _catalog_snippet(db, business.id)
    ctx = _context(business, vibe, products, services)
    system = {"content": _CONTENT_SYS, "page": _THEME_SYS, "full": _FULL_SYS}[kind]
    task = {"content": "Return the JSON now.",
            "page": "Design the theme. Return the JSON now.",
            "full": "Build the complete page. Return the JSON now."}[kind]
    user = f"{ctx}\n\n{task}"

    raw: dict = {}
    source = "fallback"
    model_used = None
    try:
        text, _tokens, model_used = await model_router.execute_with_fallback(
            messages=[{"role": "user", "content": user}],
            system_prompt=system,
            business_id=business.id,
            max_tokens=900 if kind == "full" else 500,
            temperature=0.8,
        )
        raw = _extract_json(text)
        if raw:
            source = "ai"
    except Exception as exc:
        logger.warning("Storefront generation LLM call failed",
                       business_id=str(business.id), error=f"{type(exc).__name__}: {exc}")

    result = {"kind": kind, "source": source, "model": model_used}
    if kind in ("content", "full"):
        content = _sanitize_content(raw)
        if not content and kind == "content":
            content = {"tagline": f"{business.name} — quality you can trust.",
                       "cta": "Order on Telegram"}
            result["source"] = "fallback"
        result.update(content)
    if kind in ("page", "full"):
        theme_raw = raw.get("theme") if kind == "full" else raw
        theme = _sanitize_theme(theme_raw if isinstance(theme_raw, dict) else {})
        if not theme:
            theme = _fallback_theme()
            if kind == "page":
                result["source"] = "fallback"
        result["theme"] = theme
    if kind == "full":
        sections = _sanitize_sections(raw.get("sections"))
        if sections:
            result["sections"] = sections
        if not raw:                       # nothing usable at all → true fallback
            result["source"] = "fallback"
    return result


# ── SEO metadata generation (website editor "Generate SEO") ───────────────────

_SEO_SYS = (
    "You are an SEO specialist for local businesses. Generate search-optimised "
    "website metadata. Respond with ONLY a compact JSON object (no prose, no "
    "markdown) with keys: title (<= 60 chars; include the business name + main "
    "offering, and the city if known), meta_description (<= 155 chars; "
    "compelling, with a soft call to action), hero_headline (<= 70 chars), "
    "hero_subheadline (<= 120 chars), and keywords (array of 5-8 short search "
    "phrases a customer would actually type)."
)


def _sanitize_seo(raw: dict) -> dict:
    out: dict = {}
    for key, cap in (("title", 70), ("meta_description", 160),
                     ("hero_headline", 255), ("hero_subheadline", 512)):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()[:cap]
    kws = raw.get("keywords")
    if isinstance(kws, list):
        clean = [str(k).strip()[:40] for k in kws if str(k).strip()][:8]
        if clean:
            out["keywords"] = clean
    return out


async def generate_seo(db: AsyncSession, business, vibe: str | None = None) -> dict:
    """Generate SEO metadata (title, meta description, hero copy, keywords) from
    the business data + catalog. Returns {source, model, title?, ...}. Never raises."""
    products, services = await _catalog_snippet(db, business.id)
    ctx = _context(business, vibe, products, services)
    if getattr(business, "address", None):
        ctx += f"\nLocation: {business.address}"

    raw, source, model_used = {}, "fallback", None
    try:
        text, _tokens, model_used = await model_router.execute_with_fallback(
            messages=[{"role": "user", "content": f"{ctx}\n\nReturn the JSON now."}],
            system_prompt=_SEO_SYS, business_id=business.id, max_tokens=500, temperature=0.7)
        raw = _extract_json(text)
        if raw:
            source = "ai"
    except Exception as exc:
        logger.warning("SEO generation LLM call failed", business_id=str(business.id),
                       error=f"{type(exc).__name__}: {exc}")

    result = {"source": source, "model": model_used}
    seo = _sanitize_seo(raw)
    if not seo:
        cat = getattr(business, "category", None) or "business"
        seo = {
            "title": f"{business.name} — {cat}"[:70],
            "meta_description": (getattr(business, "description", None)
                                 or f"{business.name}. Order on Telegram.")[:160],
            "hero_headline": business.name[:70],
            "keywords": [w for w in (business.name.lower(), str(cat).lower()) if w],
        }
        result["source"] = "fallback"
    result.update(seo)
    return result
