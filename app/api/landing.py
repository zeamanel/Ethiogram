# app/api/landing.py
"""SEO landing engine — a public, server-rendered website per business.

Unlike the Telegram storefront (a JS Mini App), these pages are crawlable HTML
with full SEO: <title>, meta description, canonical, OpenGraph/Twitter cards, and
JSON-LD LocalBusiness structured data. Same content source (business +
knowledge_items), rendered server-side so search engines index it.

Routes (NOT under /api/v1 — these are the public website):
    GET /biz/{slug}     the landing page
    GET /sitemap.xml    published businesses
    GET /robots.txt
"""
import html
import json
from typing import Optional

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.miniapp import _build_payload
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Business, LandingPage
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(tags=["landing"])

_CACHE_TTL = 300
_HTML_KEY = "landing:html:{slug}"


def _base_url() -> str:
    return (settings.base_url or "https://ethiogram.com").rstrip("/")


# ── JSON-LD: LocalBusiness structured data (rich results) ────────────────────

def _json_ld(business: dict, content: dict, page_url: str) -> str:
    contact = content.get("contact") or {}
    data = {
        "@context": "https://schema.org",
        "@type": "Store",
        "name": business["name"],
        "url": page_url,
        "description": business.get("tagline") or "",
    }
    if business.get("logo_url"):
        data["image"] = business["logo_url"]
    if contact.get("phone"):
        data["telephone"] = contact["phone"]
    if contact.get("address"):
        data["address"] = {"@type": "PostalAddress", "streetAddress": contact["address"]}
    products = content.get("products") or []
    if products:
        data["makesOffer"] = [
            {"@type": "Offer", "itemOffered": {"@type": "Product", "name": p["title"]},
             **({"price": p["price"]} if p.get("price") else {})}
            for p in products[:20]
        ]
    # Escape '<' so the blob can't break out of the <script> context.
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


# ── context assembly ─────────────────────────────────────────────────────────

async def _build_context(slug: str, db: AsyncSession) -> Optional[dict]:
    payload = await _build_payload(slug, db)        # reuse storefront content assembly
    if payload is None:
        return None
    business, content = payload["business"], payload["content"]

    # SEO overrides (optional) — fetched by joining LandingPage to the slug.
    lp = (await db.execute(
        select(LandingPage).join(Business, LandingPage.business_id == Business.id)
        .where(Business.slug == slug)
    )).scalar_one_or_none()

    name = business["name"]
    locality = (content.get("contact") or {}).get("address") or ""
    category = business.get("category") or "Shop"
    default_title = f"{name} — {category}" + (f" in {locality.split(',')[0]}" if locality else "")
    default_desc = business.get("tagline") or f"{name}. Order on Telegram."

    page_url = f"{_base_url()}/biz/{slug}"
    return {
        "business": business,
        "content": content,
        "theme": payload["theme"],
        "title": (lp.title if lp and lp.title else default_title)[:70],
        "meta_description": (lp.meta_description if lp and lp.meta_description else default_desc)[:160],
        "hero_headline": (lp.hero_headline if lp and lp.hero_headline else name),
        "hero_subheadline": (lp.hero_subheadline if lp and lp.hero_subheadline else (business.get("tagline") or "")),
        "keywords": (lp.seo_keywords if lp and lp.seo_keywords else []),
        "og_image": (lp.og_image_url if lp and lp.og_image_url else business.get("logo_url")),
        "is_published": bool(lp.is_published) if lp else False,
        "page_url": page_url,
        "store_url": f"{_base_url()}/app/store/?s={slug}",
        "bot_url": (f"https://t.me/{business['bot_username']}" if business.get("bot_username") else None),
        "json_ld": _json_ld(business, content, page_url),
    }


# ── template (inline so nothing extra ships in the image; autoescaped) ───────

_ENV = jinja2.Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)

_TEMPLATE = _ENV.from_string(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<meta name="description" content="{{ meta_description }}">
{% if keywords %}<meta name="keywords" content="{{ keywords | join(', ') }}">{% endif %}
{% if not is_published %}<meta name="robots" content="noindex">{% endif %}
<link rel="canonical" href="{{ page_url }}">
<meta property="og:title" content="{{ title }}">
<meta property="og:description" content="{{ meta_description }}">
<meta property="og:type" content="website">
<meta property="og:url" content="{{ page_url }}">
{% if og_image %}<meta property="og:image" content="{{ og_image }}">{% endif %}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{{ title }}">
<meta name="twitter:description" content="{{ meta_description }}">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--brand:{{ theme.primary }};--accent:{{ theme.accent }};--ink:#14110D;--muted:#6B6356;--line:#E8E1D2;--bg:#FBF8F1;--card:#fff}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;color:var(--ink);background:var(--bg);line-height:1.55}
.wrap{max-width:1040px;margin:0 auto;padding:0 20px}
h1,h2,h3{font-family:'Sora',sans-serif;letter-spacing:-0.02em;line-height:1.15}
a{color:inherit}
.nav{display:flex;align-items:center;justify-content:space-between;padding:18px 0}
.brand{display:flex;align-items:center;gap:10px;font-family:'Sora';font-weight:800;font-size:18px}
.brand .logo{width:36px;height:36px;border-radius:9px;background:var(--brand);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;overflow:hidden}
.brand .logo img{width:100%;height:100%;object-fit:cover}
.btn{display:inline-block;background:var(--brand);color:#fff;text-decoration:none;font-weight:600;font-size:14px;padding:11px 20px;border-radius:11px}
.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}
.hero{padding:48px 0 36px;display:grid;grid-template-columns:1.2fr .8fr;gap:30px;align-items:center}
.hero h1{font-size:40px;font-weight:800}.hero h1 em{color:var(--brand);font-style:normal}
.hero p{color:var(--muted);font-size:16px;margin:14px 0 22px;max-width:44ch}
.hero-cta{display:flex;gap:10px;flex-wrap:wrap}
.hero-card{background:linear-gradient(140deg,var(--brand),var(--accent));border-radius:20px;min-height:200px;display:flex;align-items:center;justify-content:center;color:#fff;font-family:'Sora';font-weight:800;font-size:22px;text-align:center;padding:24px}
.sec{padding:34px 0;border-top:1px solid var(--line)}
.sec h2{font-size:24px;font-weight:700;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.card .img{aspect-ratio:4/3;background:#F3EEE2;display:flex;align-items:center;justify-content:center;font-size:34px}
.card .img img{width:100%;height:100%;object-fit:cover}
.card .body{padding:12px 14px}
.card .name{font-weight:600;font-size:15px}
.card .desc{color:var(--muted);font-size:12.5px;margin-top:3px;min-height:1px}
.card .price{font-family:'Sora';font-weight:700;color:var(--brand);margin-top:8px}
.rows{display:flex;flex-direction:column;gap:10px}
.row{display:flex;justify-content:space-between;align-items:center;gap:14px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.row .price{font-family:'Sora';font-weight:700;color:var(--accent)}
.about p{color:var(--muted);max-width:62ch}
.faq-item{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:10px}
.faq-item .q{font-weight:600}.faq-item .a{color:var(--muted);font-size:14px;margin-top:6px}
.cta{margin:36px 0;background:var(--ink);color:#F7F3EA;border-radius:20px;padding:34px;text-align:center}
.cta h2{color:#fff;font-size:26px;margin-bottom:14px}
.contact{display:flex;gap:24px;flex-wrap:wrap;color:var(--muted);font-size:14px}
footer{padding:26px 0;color:var(--muted);font-size:13px;text-align:center;border-top:1px solid var(--line)}
@media(max-width:720px){.hero{grid-template-columns:1fr}.hero h1{font-size:30px}.hero-card{min-height:140px}}
</style>
<script type="application/ld+json">{{ json_ld | safe }}</script>
</head>
<body>
<div class="wrap">
  <nav class="nav">
    <div class="brand"><span class="logo">{% if business.logo_url %}<img src="{{ business.logo_url }}" alt="">{% else %}{{ business.name[0] | upper }}{% endif %}</span>{{ business.name }}</div>
    {% if bot_url %}<a class="btn" href="{{ bot_url }}">Order on Telegram</a>{% endif %}
  </nav>

  <header class="hero">
    <div>
      <h1>{{ hero_headline }}</h1>
      {% if hero_subheadline %}<p>{{ hero_subheadline }}</p>{% endif %}
      <div class="hero-cta">
        {% if bot_url %}<a class="btn" href="{{ bot_url }}">Order on Telegram →</a>{% endif %}
        <a class="btn ghost" href="{{ store_url }}">Browse the store</a>
      </div>
    </div>
    <div class="hero-card">{{ business.name }}</div>
  </header>

  {% if content.products %}
  <section class="sec" id="products">
    <h2>Products</h2>
    <div class="grid">
      {% for p in content.products %}
      <div class="card">
        <div class="img">{% if p.image_url %}<img src="{{ p.image_url }}" alt="{{ p.title }}">{% else %}🛍️{% endif %}</div>
        <div class="body"><div class="name">{{ p.title }}</div>
          {% if p.body %}<div class="desc">{{ p.body }}</div>{% endif %}
          {% if p.price %}<div class="price">{{ p.price }}</div>{% endif %}</div>
      </div>
      {% endfor %}
    </div>
  </section>
  {% endif %}

  {% if content.services %}
  <section class="sec" id="services">
    <h2>Services</h2>
    <div class="rows">
      {% for s in content.services %}
      <div class="row"><div><div class="name">{{ s.title }}</div>
        {% if s.body or s.duration %}<div class="desc">{{ s.body }}{% if s.duration %} · {{ s.duration }}{% endif %}</div>{% endif %}</div>
        {% if s.price %}<div class="price">{{ s.price }}</div>{% endif %}</div>
      {% endfor %}
    </div>
  </section>
  {% endif %}

  {% if business.tagline %}
  <section class="sec about" id="about"><h2>About {{ business.name }}</h2><p>{{ business.tagline }}</p></section>
  {% endif %}

  {% if content.faqs %}
  <section class="sec" id="faq">
    <h2>Frequently asked</h2>
    {% for f in content.faqs %}
    <div class="faq-item"><div class="q">{{ f.q }}</div>{% if f.a %}<div class="a">{{ f.a }}</div>{% endif %}</div>
    {% endfor %}
  </section>
  {% endif %}

  <section class="cta">
    <h2>Ready to order?</h2>
    {% if bot_url %}<a class="btn" href="{{ bot_url }}">Chat with us on Telegram</a>{% else %}<a class="btn" href="{{ store_url }}">Open the store</a>{% endif %}
  </section>

  {% if content.contact.phone or content.contact.address %}
  <section class="sec" id="contact"><h2>Visit or call</h2>
    <div class="contact">
      {% if content.contact.phone %}<div>📞 {{ content.contact.phone }}</div>{% endif %}
      {% if content.contact.address %}<div>📍 {{ content.contact.address }}</div>{% endif %}
    </div>
  </section>
  {% endif %}

  <footer>{{ business.name }} · Powered by Ethiogram</footer>
</div>
</body>
</html>""")


# ── routes ───────────────────────────────────────────────────────────────────

@router.get("/biz/{slug}", response_class=HTMLResponse)
async def landing_page(
    slug: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> HTMLResponse:
    key = _HTML_KEY.format(slug=slug)
    cached = await redis.get(key)
    if cached:
        return HTMLResponse(cached)

    ctx = await _build_context(slug, db)
    if ctx is None:
        return HTMLResponse(_not_found_html(slug), status_code=404)

    rendered = _TEMPLATE.render(**ctx)
    await redis.set(key, rendered, ex=_CACHE_TTL)
    return HTMLResponse(rendered)


def _not_found_html(slug: str) -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Not found</title>"
            f"<meta name='robots' content='noindex'></head><body style='font-family:sans-serif;"
            f"text-align:center;padding:60px'><h1>Page not found</h1>"
            f"<p>No store at <code>{html.escape(slug)}</code>.</p></body></html>")


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> PlainTextResponse:
    return PlainTextResponse(f"User-agent: *\nAllow: /\nSitemap: {_base_url()}/sitemap.xml\n")


@router.get("/sitemap.xml")
async def sitemap(db: AsyncSession = Depends(get_db)) -> Response:
    rows = (await db.execute(
        select(Business.slug)
        .join(LandingPage, LandingPage.business_id == Business.id)
        .where(Business.deleted_at.is_(None), LandingPage.is_published.is_(True))
    )).scalars().all()
    base = _base_url()
    urls = "".join(f"<url><loc>{base}/biz/{html.escape(s)}</loc></url>" for s in rows)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return Response(xml, media_type="application/xml")
