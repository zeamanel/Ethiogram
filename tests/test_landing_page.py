"""Tests for the SEO landing engine: GET /biz/{slug}, sitemap.xml, robots.txt.

Server-rendered HTML with full SEO (title, meta, OG, JSON-LD) from the same
content as the storefront. Crawlable; published pages appear in the sitemap.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.db.models import (
    Business,
    KnowledgeItem,
    KnowledgeItemType,
    LandingPage,
    User,
)


async def _biz(db, *, slug="selam-store", name="Selam Store", deleted=False, description="Best fashion in Bole"):
    uid = uuid.uuid4()
    db.add(User(id=uid))
    biz = Business(id=uuid.uuid4(), owner_id=uid, name=name, slug=slug,
                   description=description, category="Fashion",
                   phone="+251911000000", address="Bole, Addis Ababa",
                   deleted_at=datetime.now(timezone.utc) if deleted else None)
    db.add(biz)
    await db.flush()
    return biz


async def _item(db, biz_id, item_type, title, body=None, data=None):
    db.add(KnowledgeItem(id=uuid.uuid4(), business_id=biz_id, item_type=item_type,
                         title=title, body=body, data=data, is_active=True))
    await db.flush()


@pytest.mark.asyncio
async def test_landing_page_has_seo_and_content(client, db):
    biz = await _biz(db)
    await _item(db, biz.id, KnowledgeItemType.product, "Habesha Dress",
                body="Hand-woven", data={"price": "2400 ETB", "category": "Dresses"})
    await _item(db, biz.id, KnowledgeItemType.faq, "Do you deliver?", body="Yes, free over 500 ETB.")

    resp = await client.get("/biz/selam-store")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # SEO essentials
    assert "<title>Selam Store" in body
    assert '<meta name="description"' in body
    assert 'property="og:title"' in body
    assert '<link rel="canonical" href="' in body
    assert 'application/ld+json' in body
    # content rendered server-side (crawlable)
    assert "Habesha Dress" in body and "2400 ETB" in body
    assert "Do you deliver?" in body


@pytest.mark.asyncio
async def test_landing_json_ld_is_valid_store_schema(client, db):
    biz = await _biz(db, slug="jsonld")
    await _item(db, biz.id, KnowledgeItemType.product, "Bag",
                body="Leather tote", data={"price": "900 ETB", "category": "Bags",
                                           "image_url": "https://x/bag.jpg"})
    await _item(db, biz.id, KnowledgeItemType.service, "Home Cleaning",
                body="Deep clean", data={"price": "800 ETB", "duration": "2 hours"})
    body = (await client.get("/biz/jsonld")).text
    blob = body.split('application/ld+json">', 1)[1].split("</script>", 1)[0]
    data = json.loads(blob.replace("\\u003c", "<"))
    assert data["@type"] == "Store"
    assert data["telephone"] == "+251911000000"

    cat = data["hasOfferCatalog"]
    assert cat["@type"] == "OfferCatalog" and cat["numberOfItems"] == 2
    by_name = {o["itemOffered"]["name"]: o for o in cat["itemListElement"]}
    # BOTH a product and a service are indexed
    assert by_name["Bag"]["itemOffered"]["@type"] == "Product"
    assert by_name["Home Cleaning"]["itemOffered"]["@type"] == "Service"
    # rich fields + numeric price/currency parsed from "900 ETB"
    bag = by_name["Bag"]
    assert bag["price"] == "900" and bag["priceCurrency"] == "ETB"
    assert bag["itemOffered"]["image"] == "https://x/bag.jpg"
    assert bag["itemOffered"]["description"] == "Leather tote"


@pytest.mark.asyncio
async def test_landing_indexes_all_products_and_services(client, db):
    """Every item must be crawlable in HTML and present in the catalog — past
    the old 20-product cap, and including services."""
    biz = await _biz(db, slug="bigcat")
    for i in range(25):
        await _item(db, biz.id, KnowledgeItemType.product, f"Prod{i}", data={"price": f"{i + 1}00 ETB"})
    for i in range(5):
        await _item(db, biz.id, KnowledgeItemType.service, f"Svc{i}")
    body = (await client.get("/biz/bigcat")).text
    assert "Prod0" in body and "Prod24" in body and "Svc4" in body   # all rendered server-side
    blob = body.split('application/ld+json">', 1)[1].split("</script>", 1)[0]
    data = json.loads(blob.replace("\\u003c", "<"))
    assert data["hasOfferCatalog"]["numberOfItems"] == 30             # 25 products + 5 services


@pytest.mark.asyncio
async def test_landing_business_website_and_mobile(client, db):
    """Fuller business-website chrome (sticky nav, hours, map, tappable contact)
    and mobile-friendly markers."""
    from app.core.security import encrypt
    from app.db.models import Bot, BotStatus, MiniAppConfig, Platform

    biz = await _biz(db, slug="webfeat")
    biz.email = "hi@selam.com"
    biz.latitude = 9.01
    biz.longitude = 38.74
    db.add(MiniAppConfig(id=uuid.uuid4(), business_id=biz.id,
                         layout_config={"hours": "Mon–Sat, 9 AM – 6 PM"}, is_published=True))
    db.add(Bot(id=uuid.uuid4(), business_id=biz.id, platform=Platform.telegram,
               bot_username="selambot", token_hash="h" + uuid.uuid4().hex[:40],
               encrypted_token=encrypt("t"), status=BotStatus.active))
    await _item(db, biz.id, KnowledgeItemType.product, "Dress", data={"price": "1200 ETB"})
    await db.flush()

    body = (await client.get("/biz/webfeat")).text
    # mobile-friendly
    assert 'name="theme-color"' in body and "viewport-fit=cover" in body
    assert "@media(max-width:760px)" in body
    assert 'class="mobile-cta"' in body                       # sticky mobile CTA (bot present)
    # business-website chrome
    assert 'class="topbar"' in body and 'class="navlinks"' in body
    assert 'href="#products"' in body and 'href="#contact"' in body
    # contact richness
    assert "mailto:hi@selam.com" in body
    assert "tel:+251911000000" in body
    assert "Mon–Sat, 9 AM – 6 PM" in body                     # hours rendered
    # key-free OpenStreetMap embed with a marker
    assert "openstreetmap.org/export/embed.html" in body and "marker=9.01,38.74" in body


@pytest.mark.asyncio
async def test_landing_no_map_without_coords(client, db):
    biz = await _biz(db, slug="nomap")   # _biz has address+phone but no lat/lng
    await db.flush()
    body = (await client.get("/biz/nomap")).text
    assert "openstreetmap.org" not in body                    # no pin → no embedded map
    assert "🧭 Get directions" not in body or "directions" in body   # address-only still ok


@pytest.mark.asyncio
async def test_unpublished_page_is_noindex_but_viewable(client, db):
    await _biz(db, slug="draft")           # no LandingPage row → unpublished
    resp = await client.get("/biz/draft")
    assert resp.status_code == 200
    assert '<meta name="robots" content="noindex">' in resp.text   # drafts not indexed


@pytest.mark.asyncio
async def test_published_page_is_indexable(client, db):
    biz = await _biz(db, slug="live")
    db.add(LandingPage(business_id=biz.id, is_published=True))
    await db.flush()
    body = (await client.get("/biz/live")).text
    assert 'content="noindex"' not in body


@pytest.mark.asyncio
async def test_landing_404_for_unknown_or_deleted(client, db):
    assert (await client.get("/biz/nope")).status_code == 404
    await _biz(db, slug="gone", deleted=True)
    assert (await client.get("/biz/gone")).status_code == 404


@pytest.mark.asyncio
async def test_sitemap_lists_only_published(client, db):
    a = await _biz(db, slug="pub", name="Pub Co")
    db.add(LandingPage(business_id=a.id, is_published=True))
    await _biz(db, slug="unpub", name="Unpub Co")   # no LandingPage → excluded
    await db.flush()

    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert "/biz/pub" in resp.text
    assert "/biz/unpub" not in resp.text


@pytest.mark.asyncio
async def test_robots_txt(client, db):
    resp = await client.get("/robots.txt")
    assert resp.status_code == 200
    assert "User-agent: *" in resp.text
    assert "sitemap.xml" in resp.text.lower()


@pytest.mark.asyncio
async def test_landing_served_from_cache(client, db, mock_redis):
    await _biz(db, slug="cached")
    mock_redis.get.return_value = "<html>FROM CACHE</html>"
    body = (await client.get("/biz/cached")).text
    assert "FROM CACHE" in body
