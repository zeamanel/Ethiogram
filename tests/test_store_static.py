"""The customer storefront is served same-origin at /app/store/ (public)."""
import pytest


@pytest.mark.asyncio
async def test_store_index_served(client):
    resp = await client.get("/app/store/")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="store"' in body
    assert "telegram-web-app.js" in body         # WebApp SDK loaded
    assert 'id="sections"' in body               # sections container
    assert 'id="cart-bar"' in body               # client-side cart


@pytest.mark.asyncio
async def test_store_assets_served(client):
    js = await client.get("/app/store/store.js")
    assert js.status_code == 200
    assert "/api/v1/miniapp/" in js.text         # fetches the public engine
    assert "openTelegramLink" in js.text         # deep-links to the business bot
    assert "start_param" in js.text              # slug from Telegram deep link

    css = await client.get("/app/store/styles.css")
    assert css.status_code == 200
    assert "--brand" in css.text                 # theme via CSS variables


@pytest.mark.asyncio
async def test_store_renders_all_section_types(client):
    js = (await client.get("/app/store/store.js")).text
    for section in ["hero", "categories", "products", "services", "menu", "hours", "contact", "chat"]:
        assert section in js, f"missing renderer for {section}"


@pytest.mark.asyncio
async def test_store_is_no_cache(client):
    # redeploys must not be masked by Telegram's asset cache
    resp = await client.get("/app/store/store.js")
    assert "no-cache" in resp.headers.get("cache-control", "").lower()
