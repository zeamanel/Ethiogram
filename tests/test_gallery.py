"""Tests for the public Bot Gallery directory: GET /gallery and /gallery/categories."""
import uuid

import pytest

from app.db.models import Bot, BotStatus, Business, User, UserRole


async def _biz(db, *, name, slug, category=None, suspended=False, deleted=False,
               with_bot=True, bot_username="abot", bot_status=BotStatus.active):
    uid = uuid.uuid4()
    db.add(User(id=uid, role=UserRole.owner, is_active=True))
    from datetime import datetime, timezone
    biz = Business(id=uuid.uuid4(), owner_id=uid, name=name, slug=slug, category=category,
                   description=f"{name} description", is_suspended=suspended,
                   deleted_at=datetime.now(timezone.utc) if deleted else None)
    db.add(biz)
    await db.flush()
    if with_bot:
        db.add(Bot(id=uuid.uuid4(), business_id=biz.id, bot_username=bot_username,
                   encrypted_token="x", token_hash=uuid.uuid4().hex, status=bot_status))
        await db.flush()
    return biz


@pytest.mark.asyncio
async def test_gallery_is_public_and_lists_active_bot_businesses(client, db):
    await _biz(db, name="Selam Store", slug="selam", category="Fashion", bot_username="selam_bot")
    await _biz(db, name="Addis Eats", slug="addis", category="Food", bot_username="addis_bot")
    # excluded: no bot, suspended, deleted, paused bot
    await _biz(db, name="No Bot Co", slug="nobot", with_bot=False)
    await _biz(db, name="Suspended Co", slug="susp", suspended=True)
    await _biz(db, name="Gone Co", slug="gone", deleted=True)
    await _biz(db, name="Paused Co", slug="paused", bot_status=BotStatus.paused)
    await db.flush()

    resp = await client.get("/api/v1/gallery")          # NOTE: no auth header
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {i["name"] for i in body["items"]}
    assert names == {"Selam Store", "Addis Eats"}       # only active + active-bot
    item = next(i for i in body["items"] if i["slug"] == "selam")
    assert item["chat_url"] == "https://t.me/selam_bot"
    assert item["store_url"] == "/app/store/?s=selam"
    assert item["tagline"] == "Selam Store description"


@pytest.mark.asyncio
async def test_gallery_search_and_category_filter(client, db):
    await _biz(db, name="Selam Fashion", slug="s1", category="Fashion", bot_username="b1")
    await _biz(db, name="Bole Bites", slug="s2", category="Food", bot_username="b2")
    await db.flush()

    cat = (await client.get("/api/v1/gallery?category=Food")).json()
    assert {i["name"] for i in cat["items"]} == {"Bole Bites"}

    found = (await client.get("/api/v1/gallery?search=selam")).json()
    assert {i["name"] for i in found["items"]} == {"Selam Fashion"}


@pytest.mark.asyncio
async def test_gallery_categories(client, db):
    await _biz(db, name="A", slug="a", category="Fashion", bot_username="ba")
    await _biz(db, name="B", slug="b", category="Food", bot_username="bb")
    await _biz(db, name="C", slug="c", category="Fashion", bot_username="bc")
    await _biz(db, name="D", slug="d", category="Hidden", with_bot=False)  # no bot → excluded
    await db.flush()

    cats = (await client.get("/api/v1/gallery/categories")).json()
    assert cats == ["Fashion", "Food"]   # distinct, sorted, only listed businesses


@pytest.mark.asyncio
async def test_gallery_pagination(client, db):
    for i in range(5):
        await _biz(db, name=f"Biz {i}", slug=f"biz{i}", bot_username=f"bot{i}")
    await db.flush()
    page = (await client.get("/api/v1/gallery?limit=2&offset=0")).json()
    assert page["total"] == 5 and len(page["items"]) == 2
