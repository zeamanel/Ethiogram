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
    await _item(db, biz.id, KnowledgeItemType.product, "Bag", data={"price": "900 ETB"})
    body = (await client.get("/biz/jsonld")).text
    blob = body.split('application/ld+json">', 1)[1].split("</script>", 1)[0]
    data = json.loads(blob.replace("\\u003c", "<"))
    assert data["@type"] == "Store"
    assert data["name"] == "Selam Store" or data["name"] == biz.name
    assert data["telephone"] == "+251911000000"
    assert data["makesOffer"][0]["itemOffered"]["name"] == "Bag"


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
