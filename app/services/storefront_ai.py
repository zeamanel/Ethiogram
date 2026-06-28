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
    "(no prose, no markdown) with keys: tagline (<= 80 characters, punchy) and "
    "hours (a short plausible opening-hours line, or empty string if unknown)."
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
        parts.append(f"Desired vibe: {vibe}")
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
    tagline = raw.get("tagline")
    if isinstance(tagline, str) and tagline.strip():
        out["tagline"] = tagline.strip()[:120]
    hours = raw.get("hours")
    if isinstance(hours, str) and hours.strip():
        out["hours"] = hours.strip()[:120]
    return out


async def generate(db: AsyncSession, business, kind: str, vibe: str | None = None) -> dict:
    """Generate a storefront theme ("page") or copy ("content").

    Returns ``{kind, source, theme?, tagline?, hours?}`` where source is "ai"
    when the LLM produced usable output and "fallback" otherwise. Never raises.
    """
    kind = "content" if kind == "content" else "page"
    products, services = await _catalog_snippet(db, business.id)
    ctx = _context(business, vibe, products, services)
    system = _CONTENT_SYS if kind == "content" else _THEME_SYS
    user = (f"{ctx}\n\nReturn the JSON now." if kind == "content"
            else f"{ctx}\n\nDesign the theme. Return the JSON now.")

    raw: dict = {}
    source = "fallback"
    try:
        text, _tokens, _model = await model_router.execute_with_fallback(
            messages=[{"role": "user", "content": user}],
            system_prompt=system,
            business_id=business.id,
            max_tokens=400,
            temperature=0.8,
        )
        raw = _extract_json(text)
        if raw:
            source = "ai"
    except Exception as exc:
        logger.warning("Storefront generation LLM call failed",
                       business_id=str(business.id), error=f"{type(exc).__name__}: {exc}")

    result = {"kind": kind, "source": source}
    if kind == "content":
        content = _sanitize_content(raw)
        if not content:
            content = {"tagline": f"{business.name} — quality you can trust."}
            result["source"] = "fallback"
        result.update(content)
    else:
        theme = _sanitize_theme(raw)
        if not theme:
            theme = _fallback_theme()
            result["source"] = "fallback"
        result["theme"] = theme
    return result
