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
import re
from typing import Optional

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.miniapp import _build_payload
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Business, CustomDomain, LandingPage
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(tags=["landing"])

_CACHE_TTL = 300
_HTML_KEY = "landing:html:{slug}"


def _base_url() -> str:
    return (settings.base_url or "https://ethiogram.com").rstrip("/")


# ── JSON-LD: LocalBusiness structured data (rich results) ────────────────────

_PRICE_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _price_amount(price) -> Optional[str]:
    """Pull a bare numeric amount out of a free-text price ("2,400 ETB" -> 2400).
    None when there's no number to offer."""
    if not price:
        return None
    m = _PRICE_NUM.search(str(price))
    return m.group(0).replace(",", "") if m else None


def _offer(item_type: str, item: dict, currency: str) -> dict:
    """A schema.org Offer wrapping a Product or Service, with extra fields and a
    numeric price when we can parse one."""
    offered = {"@type": item_type, "name": item["title"]}
    if item.get("body"):
        offered["description"] = item["body"]
    if item.get("image_url"):
        offered["image"] = item["image_url"]
    if item.get("category"):
        offered["category"] = item["category"]
    offer = {"@type": "Offer", "itemOffered": offered}
    amount = _price_amount(item.get("price"))
    if amount:
        offer["price"] = amount
        offer["priceCurrency"] = currency
    return offer


def _osm_embed_url(lat, lng) -> Optional[str]:
    """A key-free OpenStreetMap embed (iframe src) with a marker — no API key,
    no billing. Returns None when coordinates aren't set."""
    if lat is None or lng is None:
        return None
    d = 0.004  # ~400m box around the pin
    bbox = f"{lng - d},{lat - d},{lng + d},{lat + d}"
    return ("https://www.openstreetmap.org/export/embed.html"
            f"?bbox={bbox}&layer=mapnik&marker={lat},{lng}")


def _json_ld(business: dict, content: dict, page_url: str) -> str:
    contact = content.get("contact") or {}
    currency = business.get("currency") or "ETB"
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
    if contact.get("latitude") is not None and contact.get("longitude") is not None:
        data["geo"] = {"@type": "GeoCoordinates",
                       "latitude": contact["latitude"], "longitude": contact["longitude"]}
        data["hasMap"] = contact.get("directions_url")

    # Expose the WHOLE catalog — every product AND service — as an OfferCatalog
    # so search engines can index each item (not just a truncated product list).
    offers = [_offer("Product", p, currency) for p in (content.get("products") or [])]
    offers += [_offer("Service", s, currency) for s in (content.get("services") or [])]
    if offers:
        data["hasOfferCatalog"] = {
            "@type": "OfferCatalog",
            "name": f"{business['name']} catalog",
            "numberOfItems": len(offers),
            "itemListElement": offers[:200],
        }
    # Escape '<' so the blob can't break out of the <script> context.
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


# ── context assembly ─────────────────────────────────────────────────────────

async def _build_context(slug: str, db: AsyncSession, *,
                         page_url: str, site_base: str) -> Optional[dict]:
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

    contact = content.get("contact") or {}
    has_visit = bool(contact.get("phone") or contact.get("email") or contact.get("address")
                     or contact.get("directions_url") or content.get("hours"))

    return {
        "business": business,
        "content": content,
        "theme": payload["theme"],
        "has_visit": has_visit,
        "map_embed_url": _osm_embed_url(contact.get("latitude"), contact.get("longitude")),
        "title": (lp.title if lp and lp.title else default_title)[:70],
        "meta_description": (lp.meta_description if lp and lp.meta_description else default_desc)[:160],
        "hero_headline": (lp.hero_headline if lp and lp.hero_headline else name),
        "hero_subheadline": (lp.hero_subheadline if lp and lp.hero_subheadline else (business.get("tagline") or "")),
        "keywords": (lp.seo_keywords if lp and lp.seo_keywords else []),
        "og_image": (lp.og_image_url if lp and lp.og_image_url else business.get("logo_url")),
        "is_published": bool(lp.is_published) if lp else False,
        "page_url": page_url,
        "store_url": f"{site_base}/app/store/?s={slug}",
        "bot_url": (f"https://t.me/{business['bot_username']}" if business.get("bot_username") else None),
        # Acquisition: every landing page links back to the onboarding bot, with
        # the source slug in the /start payload so signups are attributable.
        "ethiogram_url": f"https://t.me/{settings.master_bot_username}?start=web_{slug}",
        "json_ld": _json_ld(business, content, page_url),
    }


# ── template (inline so nothing extra ships in the image; autoescaped) ───────

_ENV = jinja2.Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)

_TEMPLATE = _ENV.from_string(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="{{ theme.primary }}">
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
html{scroll-behavior:smooth}
body{font-family:'Inter',system-ui,sans-serif;color:var(--ink);background:var(--bg);line-height:1.55;-webkit-text-size-adjust:100%}
img{max-width:100%;height:auto}
.wrap{max-width:1040px;margin:0 auto;padding:0 20px}
h1,h2,h3{font-family:'Sora',sans-serif;letter-spacing:-0.02em;line-height:1.15}
a{color:inherit}
/* sticky header */
.topbar{position:sticky;top:0;z-index:50;background:rgba(251,248,241,.92);backdrop-filter:saturate(140%) blur(10px);border-bottom:1px solid var(--line)}
.topbar-in{display:flex;align-items:center;gap:14px;padding:12px 0}
.brand{display:flex;align-items:center;gap:10px;font-family:'Sora';font-weight:800;font-size:17px;text-decoration:none}
.brand .logo{width:34px;height:34px;border-radius:9px;background:var(--brand);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;overflow:hidden;flex-shrink:0}
.brand .logo img{width:100%;height:100%;object-fit:cover}
.navlinks{display:flex;gap:18px;margin-left:auto;font-size:14px;font-weight:600}
.navlinks a{color:var(--muted);text-decoration:none}
.navlinks a:hover{color:var(--ink)}
.btn{display:inline-block;background:var(--brand);color:#fff;text-decoration:none;font-weight:600;font-size:14px;padding:11px 20px;border-radius:11px}
.btn.sm{padding:9px 15px;font-size:13px}
.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}
.hero{padding:44px 0 36px;display:grid;grid-template-columns:1.2fr .8fr;gap:30px;align-items:center}
.hero h1{font-size:40px;font-weight:800}.hero h1 em{color:var(--brand);font-style:normal}
.hero p{color:var(--muted);font-size:16px;margin:14px 0 22px;max-width:44ch}
.hero-cta{display:flex;gap:10px;flex-wrap:wrap}
.hero-card{background:linear-gradient(140deg,var(--brand),var(--accent));border-radius:20px;min-height:200px;display:flex;align-items:center;justify-content:center;color:#fff;font-family:'Sora';font-weight:800;font-size:22px;text-align:center;padding:24px}
.sec{padding:34px 0;border-top:1px solid var(--line)}
.sec h2{font-size:24px;font-weight:700;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.card .img{aspect-ratio:4/3;background:#F3EEE2;display:flex;align-items:center;justify-content:center;font-size:34px}
.card .img img{width:100%;height:100%;object-fit:cover}
.card .body{padding:12px 14px}
.card .name{font-weight:600;font-size:15px}
.card .desc{color:var(--muted);font-size:12.5px;margin-top:3px;min-height:1px}
.card .price{font-family:'Sora';font-weight:700;color:var(--brand);margin-top:8px}
.rows{display:flex;flex-direction:column;gap:10px}
.row{display:flex;justify-content:space-between;align-items:center;gap:14px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.row .price{font-family:'Sora';font-weight:700;color:var(--accent);white-space:nowrap}
.about p{color:var(--muted);max-width:62ch}
.faq-item{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:10px}
.faq-item .q{font-weight:600}.faq-item .a{color:var(--muted);font-size:14px;margin-top:6px}
.cta{margin:36px 0;background:var(--ink);color:#F7F3EA;border-radius:20px;padding:34px;text-align:center}
.cta h2{color:#fff;font-size:26px;margin-bottom:14px}
.visit-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}
.contact{display:flex;flex-direction:column;gap:12px;color:var(--ink);font-size:15px}
.info{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink)}
.info .ic{width:30px;text-align:center;font-size:17px;flex-shrink:0}
.info.link:hover{color:var(--brand)}
.map{border-radius:14px;overflow:hidden;border:1px solid var(--line);height:300px}
.map iframe{width:100%;height:100%;border:0;display:block}
footer{padding:26px 0 34px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;justify-content:space-between}
.foot-links{display:flex;gap:14px;flex-wrap:wrap}
.foot-links a{color:var(--muted);text-decoration:none}
.mobile-cta{display:none}
@media(max-width:760px){
  .hero{grid-template-columns:1fr;padding:30px 0 26px}
  .hero h1{font-size:29px}.hero p{font-size:15px}
  .hero-card{min-height:130px;font-size:18px;order:-1}
  .navlinks{display:none}
  .sec{padding:26px 0}.sec h2{font-size:21px}
  .grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
  .cta{padding:26px 20px}.cta h2{font-size:21px}
  .visit-grid{grid-template-columns:1fr}
  .map{height:230px}
  footer{justify-content:center;text-align:center}
  body{padding-bottom:74px}
  .mobile-cta{display:flex;position:fixed;left:14px;right:14px;bottom:14px;z-index:60;
    align-items:center;justify-content:center;gap:8px;background:var(--brand);color:#fff;
    text-decoration:none;font-weight:700;font-size:15px;padding:15px;border-radius:14px;
    box-shadow:0 8px 24px rgba(0,0,0,.18)}
}
/* support chat widget */
.echat-fab{position:fixed;right:18px;bottom:24px;z-index:70;width:56px;height:56px;border-radius:50%;background:var(--brand);color:#fff;border:none;cursor:pointer;font-size:24px;box-shadow:0 8px 24px rgba(0,0,0,.22);display:flex;align-items:center;justify-content:center}
.echat-panel{position:fixed;right:18px;bottom:90px;z-index:71;width:360px;max-width:calc(100vw - 36px);height:520px;max-height:calc(100vh - 130px);background:var(--card);border:1px solid var(--line);border-radius:18px;box-shadow:0 16px 48px rgba(0,0,0,.24);display:none;flex-direction:column;overflow:hidden}
.echat-panel.open{display:flex}
.echat-head{background:var(--brand);color:#fff;padding:13px 15px;display:flex;align-items:center;gap:9px;font-size:18px}
.echat-head .t{font-family:'Sora';font-weight:700;font-size:15px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.echat-head .x{background:none;border:none;color:#fff;font-size:22px;line-height:1;cursor:pointer;opacity:.85}
.echat-body{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:9px;background:var(--bg)}
.echat-msg{max-width:84%;padding:9px 13px;border-radius:14px;font-size:14px;line-height:1.45;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
.echat-msg.bot{background:var(--card);border:1px solid var(--line);align-self:flex-start;border-bottom-left-radius:5px}
.echat-msg.me{background:var(--brand);color:#fff;align-self:flex-end;border-bottom-right-radius:5px}
.echat-msg.typing{color:var(--muted)}
.echat-foot{border-top:1px solid var(--line);padding:10px;display:flex;gap:8px;background:var(--card)}
.echat-foot input{flex:1;min-width:0;border:1px solid var(--line);border-radius:11px;padding:11px 12px;font-size:14px;font-family:inherit;outline:none}
.echat-foot input:focus{border-color:var(--brand)}
.echat-foot button{background:var(--brand);color:#fff;border:none;border-radius:11px;padding:0 16px;font-weight:600;cursor:pointer}
.echat-tg{display:block;text-align:center;font-size:12.5px;color:var(--muted);text-decoration:none;padding:9px;border-top:1px solid var(--line);background:var(--card)}
@media(max-width:760px){
  .echat-fab{bottom:86px}
  .echat-panel{right:10px;left:10px;top:10px;bottom:10px;width:auto;max-width:none;height:auto;max-height:none}
}
</style>
<script type="application/ld+json">{{ json_ld | safe }}</script>
</head>
<body id="top">
  <header class="topbar">
    <div class="wrap topbar-in">
      <a class="brand" href="#top"><span class="logo">{% if business.logo_url %}<img src="{{ business.logo_url }}" alt="">{% else %}{{ business.name[0] | upper }}{% endif %}</span>{{ business.name }}</a>
      <nav class="navlinks">
        {% if content.products %}<a href="#products">Products</a>{% endif %}
        {% if content.services %}<a href="#services">Services</a>{% endif %}
        {% if business.tagline %}<a href="#about">About</a>{% endif %}
        {% if has_visit %}<a href="#contact">Visit</a>{% endif %}
      </nav>
      {% if bot_url %}<a class="btn sm" href="{{ bot_url }}">Order</a>{% endif %}
    </div>
  </header>
<div class="wrap">
  <header class="hero">
    <div>
      <h1>{{ hero_headline }}</h1>
      {% if hero_subheadline %}<p>{{ hero_subheadline }}</p>{% endif %}
      <div class="hero-cta">
        {% if bot_url %}<a class="btn" href="{{ bot_url }}">Order on Telegram →</a>{% endif %}
        <a class="btn ghost" href="{{ store_url }}">Browse the store</a>
      </div>
    </div>
    <div class="hero-card">{% if business.logo_url %}<img src="{{ business.logo_url }}" alt="{{ business.name }}" style="max-height:120px;border-radius:14px">{% else %}{{ business.name }}{% endif %}</div>
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

  {% if has_visit %}
  <section class="sec" id="contact"><h2>Visit or call</h2>
    <div class="visit-grid">
      <div class="contact">
        {% if content.contact.phone %}<a class="info link" href="tel:{{ content.contact.phone }}"><span class="ic">📞</span><span>{{ content.contact.phone }}</span></a>{% endif %}
        {% if content.contact.email %}<a class="info link" href="mailto:{{ content.contact.email }}"><span class="ic">✉️</span><span>{{ content.contact.email }}</span></a>{% endif %}
        {% if content.contact.address %}<div class="info"><span class="ic">📍</span><span>{{ content.contact.address }}</span></div>{% endif %}
        {% if content.hours %}<div class="info"><span class="ic">🕒</span><span>{{ content.hours }}</span></div>{% endif %}
        {% if content.contact.directions_url %}<a class="info link" href="{{ content.contact.directions_url }}" target="_blank" rel="noopener"><span class="ic">🧭</span><span>Get directions</span></a>{% endif %}
      </div>
      {% if map_embed_url %}<div class="map"><iframe src="{{ map_embed_url }}" loading="lazy" title="Map of {{ business.name }}"></iframe></div>{% endif %}
    </div>
  </section>
  {% endif %}

  <footer>
    <div>© {{ business.name }} · <a href="{{ ethiogram_url }}" rel="noopener">⚡ Powered by Ethiogram — get a bot like this</a></div>
    <div class="foot-links">
      {% if content.products %}<a href="#products">Products</a>{% endif %}
      {% if content.services %}<a href="#services">Services</a>{% endif %}
      {% if has_visit %}<a href="#contact">Contact</a>{% endif %}
      <a href="{{ store_url }}">Store</a>
    </div>
  </footer>
</div>
{% if bot_url %}<a class="mobile-cta" href="{{ bot_url }}">💬 Order on Telegram</a>{% endif %}

<!-- support chat widget -->
<div id="echat" data-slug="{{ business.slug }}" data-name="{{ business.name }}"></div>
<button class="echat-fab" id="echat-fab" aria-label="Chat with us">💬</button>
<div class="echat-panel" id="echat-panel" role="dialog" aria-label="Support chat">
  <div class="echat-head"><span>💬</span><span class="t">Chat with {{ business.name }}</span><button class="x" id="echat-close" aria-label="Close">×</button></div>
  <div class="echat-body" id="echat-body"></div>
  <div class="echat-foot">
    <input id="echat-input" type="text" placeholder="Ask a question…" autocomplete="off" maxlength="800">
    <button id="echat-send">Send</button>
  </div>
  {% if bot_url %}<a class="echat-tg" href="{{ bot_url }}" target="_blank" rel="noopener">Continue on Telegram →</a>{% endif %}
</div>
<script>
(function(){
  var el=document.getElementById('echat'); if(!el) return;
  var slug=el.dataset.slug, name=el.dataset.name||'us';
  var panel=document.getElementById('echat-panel'), body=document.getElementById('echat-body'),
      input=document.getElementById('echat-input'), fab=document.getElementById('echat-fab');
  var history=[], busy=false, greeted=false;
  function add(role,text){
    var m=document.createElement('div');
    m.className='echat-msg '+(role==='user'?'me':'bot');
    m.textContent=text; body.appendChild(m); body.scrollTop=body.scrollHeight; return m;
  }
  function openPanel(){
    panel.classList.add('open');
    if(!greeted){greeted=true; add('assistant','Hi! 👋 Ask me anything about '+name+' — hours, services, prices…');}
    input.focus();
  }
  function closePanel(){ panel.classList.remove('open'); }
  fab.onclick=function(){ panel.classList.contains('open')?closePanel():openPanel(); };
  document.getElementById('echat-close').onclick=closePanel;
  async function send(){
    var msg=(input.value||'').trim(); if(!msg||busy) return;
    input.value=''; add('user',msg); busy=true;
    var typing=add('assistant','…'); typing.classList.add('typing');
    try{
      var res=await fetch('/api/v1/miniapp/'+encodeURIComponent(slug)+'/chat',{
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({message:msg, history:history.slice(-6)})});
      var data=await res.json().catch(function(){return {};});
      typing.remove();
      if(res.status===429){ add('assistant',"You're sending messages a bit fast — please wait a moment and try again."); }
      else{
        var reply=(data&&data.reply)?data.reply:"Sorry, please try again or continue on Telegram.";
        add('assistant',reply);
        history.push({role:'user',content:msg}); history.push({role:'assistant',content:reply});
      }
    }catch(e){ typing.remove(); add('assistant',"Sorry, I couldn't reach the assistant. Please continue on Telegram."); }
    finally{ busy=false; input.focus(); }
  }
  document.getElementById('echat-send').onclick=send;
  input.addEventListener('keydown',function(e){ if(e.key==='Enter'){ e.preventDefault(); send(); } });
})();
</script>
</body>
</html>""")


# ── routes ───────────────────────────────────────────────────────────────────

async def _render_landing(slug: str, db, redis, *, page_url: str, site_base: str,
                          cache_key: str) -> Optional[str]:
    """Render (or serve cached) HTML for a slug. None if the business is gone."""
    cached = await redis.get(cache_key)
    if cached:
        return cached
    ctx = await _build_context(slug, db, page_url=page_url, site_base=site_base)
    if ctx is None:
        return None
    rendered = _TEMPLATE.render(**ctx)
    await redis.set(cache_key, rendered, ex=_CACHE_TTL)
    return rendered


@router.get("/biz/{slug}", response_class=HTMLResponse)
async def landing_page(
    slug: str,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> HTMLResponse:
    base = _base_url()
    html_out = await _render_landing(
        slug, db, redis,
        page_url=f"{base}/biz/{slug}", site_base=base, cache_key=_HTML_KEY.format(slug=slug),
    )
    if html_out is None:
        return HTMLResponse(_not_found_html(slug), status_code=404)
    return HTMLResponse(html_out)


async def _slug_for_host(host: str, db: AsyncSession) -> Optional[str]:
    """Resolve a verified, active custom domain to its business slug."""
    return await db.scalar(
        select(Business.slug)
        .join(CustomDomain, CustomDomain.business_id == Business.id)
        .where(CustomDomain.domain == host,
               CustomDomain.is_verified.is_(True), CustomDomain.is_active.is_(True),
               Business.deleted_at.is_(None))
    )


@router.get("/", response_class=HTMLResponse)
async def root(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> HTMLResponse:
    """On a verified custom domain, '/' serves that business's landing page.
    On the platform host, a minimal placeholder."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    slug = await _slug_for_host(host, db) if host else None
    if slug:
        site_base = f"https://{host}"
        html_out = await _render_landing(
            slug, db, redis,
            page_url=f"{site_base}/", site_base=site_base, cache_key=f"landing:host:{host}",
        )
        if html_out is not None:
            return HTMLResponse(html_out)
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Ethiogram</title></head>"
        "<body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h1>Ethiogram</h1><p>AI commerce for Telegram.</p></body></html>"
    )


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
    urls = f"<url><loc>{base}/directory</loc></url>" + "".join(
        f"<url><loc>{base}/biz/{html.escape(s)}</loc></url>" for s in rows)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return Response(xml, media_type="application/xml")


# ── public business directory — a crawlable index page that links every
# published business site, grouped by category. Compounds SEO: one internal
# hub page passing link equity to every member business (and to Ethiogram).
_DIRECTORY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Business Directory — Ethiopian businesses on Ethiogram</title>
<meta name="description" content="Discover Ethiopian businesses — shops, salons, restaurants, clinics — with AI assistants on Telegram. Browse by category.">
<link rel="canonical" href="{base}/directory">
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#faf8f4;color:#1c1917}}
.wrap{{max-width:760px;margin:0 auto;padding:28px 18px 60px}}
h1{{font-size:26px;margin:0 0 6px}} .sub{{color:#78716c;margin:0 0 26px}}
h2{{font-size:15px;color:#a16207;text-transform:uppercase;letter-spacing:.06em;margin:26px 0 10px}}
a.biz{{display:block;background:#fff;border:1px solid #e7e5e4;border-radius:12px;
padding:13px 15px;margin-bottom:8px;text-decoration:none;color:inherit}}
a.biz b{{display:block}} a.biz span{{font-size:13px;color:#78716c}}
footer{{margin-top:40px;font-size:13px;color:#78716c}}
footer a{{color:#a16207;text-decoration:none;font-weight:600}}
</style></head><body><div class="wrap">
<h1>Business Directory</h1>
<p class="sub">Ethiopian businesses serving customers with AI on Telegram.</p>
{groups}
<footer><a href="{cta}" rel="noopener">⚡ Get an AI bot for your business — free to start</a></footer>
</div></body></html>"""


@router.get("/directory", response_class=HTMLResponse)
async def directory(db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    rows = (await db.execute(
        select(Business.slug, Business.name, Business.category, Business.description)
        .join(LandingPage, LandingPage.business_id == Business.id)
        .where(Business.deleted_at.is_(None), LandingPage.is_published.is_(True))
        .order_by(Business.category, Business.name)
    )).all()

    by_cat: dict[str, list] = {}
    for slug, name, category, desc in rows:
        by_cat.setdefault((category or "Other").strip().title(), []).append((slug, name, desc))

    groups = "".join(
        f"<h2>{html.escape(cat)}</h2>" + "".join(
            f'<a class="biz" href="/biz/{html.escape(slug)}"><b>{html.escape(name)}</b>'
            + (f"<span>{html.escape(desc[:110])}</span>" if desc else "") + "</a>"
            for slug, name, desc in items)
        for cat, items in sorted(by_cat.items())
    ) or "<p>No businesses listed yet — be the first!</p>"

    cta = f"https://t.me/{settings.master_bot_username}?start=directory"
    return HTMLResponse(_DIRECTORY_HTML.format(base=_base_url(), groups=groups, cta=cta))
