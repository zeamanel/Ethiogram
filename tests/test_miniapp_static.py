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
    assert "Something went wrong" in tg.text  # friendly message on 5xx


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
async def test_business_switcher_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="biz-switcher"' in html and 'id="biz-switch-menu"' in html   # picker + dropdown
    js = (await client.get("/app/owner/app.js")).text
    assert "renderSwitcher" in js and "switchBusiness" in js                # picker wired
    assert "loadDashboard" in js and "pickBusiness" in js                   # reload per business
    assert "localStorage" in js and "eth_selected_biz" in js               # selection persisted
    assert "businesses.length < 2" in js                                    # hidden for single business


@pytest.mark.asyncio
async def test_account_page_served(client):
    html = (await client.get("/app/account/")).text
    assert 'id="account"' in html and 'id="a-ref-link"' in html
    assert "telegram-web-app.js" in html
    js = (await client.get("/app/account/account.js")).text
    assert "/account/me" in js and "Eth.login" in js
    css = await client.get("/app/account/styles.css")
    assert css.status_code == 200


@pytest.mark.asyncio
async def test_legal_pages_served(client):
    priv = await client.get("/app/legal/privacy.html")
    assert priv.status_code == 200 and "Privacy Policy" in priv.text
    terms = await client.get("/app/legal/terms.html")
    assert terms.status_code == 200 and "Terms of Service" in terms.text


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
    assert 'id="se-logo-file"' in html and "logo_url" in js                  # business logo upload
    # store vibe (ui_child_prompt) + font selectors
    assert 'id="se-vibe"' in html and 'id="se-font-heading"' in html and 'id="se-font-body"' in html
    assert "ui_child_prompt" in js and "font_heading" in js
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
    # custom domain connect/verify within the website editor
    assert 'id="dom-panel"' in html
    assert "loadDomain" in js and "/domain/verify" in js and "dns_records" in js


@pytest.mark.asyncio
async def test_billing_editor_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="billing-editor"' in html and 'id="bl-policy"' in html       # editor + policy
    assert 'id="bl-limit"' in html and 'id="bl-usage-list"' in html         # cap + usage table
    assert 'id="billing-customize"' in html                                 # dashboard entry
    js = (await client.get("/app/owner/app.js")).text
    assert "openBilling" in js and "/billing" in js and "/users/usage" in js
    # owner-managed customer credit (end-user recharge v1)
    assert 'id="bl-credit"' in html and "openCredit" in js and "/credit" in js


@pytest.mark.asyncio
async def test_admin_dashboard_present(client):
    html = (await client.get("/app/owner/")).text
    assert 'id="admin"' in html and 'class="adm-tabs"' in html              # admin container + tabs
    assert 'id="adm-biz-list"' in html and 'id="adm-user-list"' in html     # businesses + users tables
    assert 'id="adm-system-list"' in html                                   # system tab
    js = (await client.get("/app/owner/app.js")).text
    assert "bootAdmin" in js and "/admin/stats" in js                       # admin boot + stats
    assert "auth.is_admin" in js                                            # branch on admin
    assert "/admin/businesses/" in js and "/admin/users/" in js             # suspend/delete + admin toggle
    # agents tab: set a father agent's model
    assert 'data-tab="agents"' in html and 'id="adm-agents-list"' in html
    assert "loadAdminAgents" in js and "/admin/agents/" in js and "/admin/models" in js


@pytest.mark.asyncio
async def test_app_responses_are_no_cache(client):
    # redeploys should not be masked by Telegram's asset cache
    resp = await client.get("/app/owner/app.js")
    assert "no-cache" in resp.headers.get("cache-control", "").lower()
