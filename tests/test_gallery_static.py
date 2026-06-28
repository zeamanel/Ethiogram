"""The public Bot Gallery is served same-origin at /app/gallery/ (no auth)."""
import pytest


@pytest.mark.asyncio
async def test_gallery_page_served(client):
    resp = await client.get("/app/gallery/")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="g-list"' in body and 'id="g-search"' in body and 'id="g-chips"' in body
    assert "telegram-web-app.js" in body


@pytest.mark.asyncio
async def test_gallery_assets_served(client):
    js = await client.get("/app/gallery/gallery.js")
    assert js.status_code == 200
    assert "/gallery" in js.text and "openTelegramLink" in js.text   # fetches dir + deep-links to chat
    assert "/api/v1" in js.text                                       # no-auth public fetch
    css = await client.get("/app/gallery/styles.css")
    assert css.status_code == 200


@pytest.mark.asyncio
async def test_gallery_is_no_cache(client):
    resp = await client.get("/app/gallery/gallery.js")
    assert "no-cache" in resp.headers.get("cache-control", "").lower()
