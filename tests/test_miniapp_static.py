"""The owner Mini App static files are served same-origin at /app."""
import pytest


@pytest.mark.asyncio
async def test_owner_index_served(client):
    resp = await client.get("/app/owner/")
    assert resp.status_code == 200
    body = resp.text
    assert "Owner Console" in body
    assert 'id="dashboard"' in body          # the dashboard container
    assert "telegram-web-app.js" in body     # WebApp SDK loaded


@pytest.mark.asyncio
async def test_owner_assets_served(client):
    js = await client.get("/app/owner/app.js")
    assert js.status_code == 200
    assert "dashboard/overview" in js.text   # calls the real endpoint

    css = await client.get("/app/owner/styles.css")
    assert css.status_code == 200

    tg = await client.get("/app/shared/tg.js")
    assert tg.status_code == 200
    assert "/api/v1/auth/miniapp" in tg.text  # owner auth path
