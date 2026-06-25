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


@pytest.mark.asyncio
async def test_onboarding_wizard_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="wiz-business"' in html and 'id="wiz-bot"' in html   # both wizard steps
    js = (await client.get("/app/owner/app.js")).text
    assert "/businesses" in js and "/bots" in js                     # wizard posts to both
    tg = (await client.get("/app/shared/tg.js")).text
    assert "async post(" in tg                                        # POST helper exists


@pytest.mark.asyncio
async def test_brain_manager_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="brain-manager"' in html and 'id="bm-upload"' in html  # manager + upload control
    js = (await client.get("/app/owner/app.js")).text
    assert "openBrainManager" in js and "knowledge/" in js            # upload/list/delete wired
    tg = (await client.get("/app/shared/tg.js")).text
    assert "async upload(" in tg and "async del(" in tg               # multipart + delete helpers


@pytest.mark.asyncio
async def test_app_responses_are_no_cache(client):
    # redeploys should not be masked by Telegram's asset cache
    resp = await client.get("/app/owner/app.js")
    assert "no-cache" in resp.headers.get("cache-control", "").lower()
