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
async def test_brain_settings_editor_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="brain-settings"' in html and 'id="bs-thresh"' in html   # settings view + threshold field
    js = (await client.get("/app/owner/app.js")).text
    assert "openBrainSettings" in js and "/brain" in js                 # GET/PATCH wired
    tg = (await client.get("/app/shared/tg.js")).text
    assert "async patch(" in tg                                          # PATCH helper exists


@pytest.mark.asyncio
async def test_catalog_manager_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="catalog-manager"' in html and 'id="cat-form"' in html   # view + add/edit form
    assert 'id="cat-type"' in html and 'id="cat-price"' in html         # type + price fields
    js = (await client.get("/app/owner/app.js")).text
    assert "openCatalog" in js and "/items" in js                       # CRUD wired
    assert "loadItems" in js
    # category + image upload
    assert 'id="cat-category"' in html and 'id="cat-image-file"' in html
    assert "/items/image" in js and "image_url" in js                   # upload + store URL


@pytest.mark.asyncio
async def test_agent_manager_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="agent-manager"' in html and 'id="ag-active"' in html   # view + active toggle
    assert 'id="ag-fields"' in html and 'id="ag-name"' in html         # config + rename
    js = (await client.get("/app/owner/app.js")).text
    assert "openAgentManager" in js and "/agents/child/" in js          # GET/PATCH wired
    assert 'data-agent=' in js                                          # rows are clickable
    # write-only credentials editor + disconnect affordance
    assert 'id="ag-secrets-sec"' in html and 'id="ag-secret-fields"' in html
    assert 'id="ag-secrets-disconnect"' in html
    assert "secretFieldDefs" in js and "child_secrets" in js
    assert "/secrets" in js and "confirmAction" in js                   # disconnect wired


@pytest.mark.asyncio
async def test_browse_and_deploy_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="agents-browse"' in html and 'id="agent-deploy"' in html   # both views
    assert 'id="agents-browse-link"' in html and 'id="ad-deploy"' in html # entry + deploy button
    js = (await client.get("/app/owner/app.js")).text
    assert "openAgentsBrowse" in js and "openAgentDeploy" in js
    assert '/trial' in js and 'Eth.get("/agents")' in js                  # lists marketplace + starts trial


@pytest.mark.asyncio
async def test_storefront_editor_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="storefront-editor"' in html and 'id="se-sections"' in html   # view + section list
    assert 'id="se-primary"' in html and 'id="se-published"' in html         # theme + publish
    assert 'id="store-customize"' in html                                    # dashboard entry
    js = (await client.get("/app/owner/app.js")).text
    assert "openStorefront" in js and "/storefront" in js                    # GET/PATCH wired
    assert "SF_SECTIONS" in js                                               # reorder/toggle state
    # publish reveals the live link + BotFather setup guide
    assert 'id="se-publish-info"' in html and 'id="se-store-url"' in html
    assert "BotFather" in html and "Menu Button" in html
    assert "togglePublishInfo" in js and "clipboard" in js                   # toggle + copy wired


@pytest.mark.asyncio
async def test_website_editor_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="website-editor"' in html and 'id="we-title"' in html       # view + SEO title
    assert 'id="we-meta"' in html and 'id="we-og-file"' in html            # meta + OG image
    assert 'id="web-customize"' in html                                    # dashboard entry
    js = (await client.get("/app/owner/app.js")).text
    assert "openWebsite" in js and "/website" in js                        # GET/PATCH wired
    assert "seo_keywords" in js                                            # keywords serialized


@pytest.mark.asyncio
async def test_app_responses_are_no_cache(client):
    # redeploys should not be masked by Telegram's asset cache
    resp = await client.get("/app/owner/app.js")
    assert "no-cache" in resp.headers.get("cache-control", "").lower()
