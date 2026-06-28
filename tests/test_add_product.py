"""Tests for the in-bot Add-Product flow: caption parsing, owner-only gate, and
the end-to-end creation of a catalog item from a captioned photo."""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.api.webhooks as wh
from app.db.models import Business, KnowledgeItem, KnowledgeItemType, User, UserRole


# ── caption parsing ──────────────────────────────────────────────────────────

def test_parse_full_caption():
    p = wh._parse_product_caption("Title: Blue Dress\nPrice: 1200 ETB\nCategory: Dresses\nFlowing cotton")
    assert p["title"] == "Blue Dress"
    assert p["price"] == "1200 ETB"
    assert p["category"] == "Dresses"
    assert p["body"] == "Flowing cotton"


def test_parse_title_only():
    p = wh._parse_product_caption("Title: Red Shoes")
    assert p["title"] == "Red Shoes" and p["price"] is None and p["category"] is None


def test_parse_requires_title():
    assert wh._parse_product_caption("just a nice photo") is None
    assert wh._parse_product_caption("Price: 100") is None
    assert wh._parse_product_caption("") is None


# ── owner gate ───────────────────────────────────────────────────────────────

def _env(customer_id, *, media="photo", file_id="f1", text="Title: X\nPrice: 5 ETB"):
    return SimpleNamespace(customer_id=str(customer_id), media_type=media,
                           media_file_id=file_id, text=text, chat_type="private")


@pytest.mark.asyncio
async def test_sender_is_owner(db):
    owner = User(id=uuid.uuid4(), telegram_id=4242, role=UserRole.owner, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="B", slug="b1")
    db.add(biz)
    await db.flush()
    assert await wh._sender_is_owner(_env(4242), biz, db) is True
    assert await wh._sender_is_owner(_env(9999), biz, db) is False   # a customer


# ── end-to-end (image download + upload mocked) ──────────────────────────────

@pytest.fixture
def _stub_media(monkeypatch):
    async def _url(token, file_id):
        return "https://api.telegram.org/file/x"
    async def _upload(data, path, content_type=None):
        return f"https://storage.googleapis.com/ethiogram-public/{path}"
    monkeypatch.setattr(wh.telegram_service, "get_file_download_url", _url)
    monkeypatch.setattr(wh.storage_service, "upload_public", _upload)

    class _Resp:
        content = b"\x89PNG fake"
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()
    monkeypatch.setattr(wh._httpx, "AsyncClient", _Client)

    sent = {}
    async def _send(token, chat_id, text):
        sent["text"] = text
    monkeypatch.setattr(wh.telegram_service, "send_message", _send)
    return sent


async def _owned_biz(db, tg_id=4242):
    owner = User(id=uuid.uuid4(), telegram_id=tg_id, role=UserRole.owner, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="B", slug=f"b-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_owner_photo_creates_product(db, mock_redis, _stub_media):
    biz = await _owned_biz(db, 4242)
    handled = await wh._maybe_handle_add_product(
        _env(4242, text="Title: Blue Dress\nPrice: 1200 ETB\nCategory: Dresses"),
        bot=SimpleNamespace(business_id=biz.id), business=biz, raw_token="tok", db=db, redis=mock_redis)
    assert handled is True
    item = (await db.execute(select(KnowledgeItem).where(
        KnowledgeItem.business_id == biz.id))).scalars().one()
    assert item.item_type == KnowledgeItemType.product
    assert item.title == "Blue Dress"
    assert item.data["price"] == "1200 ETB" and item.data["category"] == "Dresses"
    assert item.data["image_url"].startswith("https://storage.googleapis.com/")
    assert "Added" in _stub_media["text"]


@pytest.mark.asyncio
async def test_customer_photo_is_ignored(db, mock_redis, _stub_media):
    biz = await _owned_biz(db, 4242)
    handled = await wh._maybe_handle_add_product(
        _env(9999, text="Title: Sneaky Item\nPrice: 1"),   # NOT the owner
        bot=SimpleNamespace(business_id=biz.id), business=biz, raw_token="tok", db=db, redis=mock_redis)
    assert handled is False
    assert (await db.execute(select(KnowledgeItem))).first() is None   # nothing written


@pytest.mark.asyncio
async def test_addproduct_command_replies_help(db, mock_redis, _stub_media):
    biz = await _owned_biz(db, 4242)
    handled = await wh._maybe_handle_add_product(
        _env(4242, media=None, file_id=None, text="/addproduct"),
        bot=SimpleNamespace(business_id=biz.id), business=biz, raw_token="tok", db=db, redis=mock_redis)
    assert handled is True
    assert "photo" in _stub_media["text"].lower()
    assert (await db.execute(select(KnowledgeItem))).first() is None   # help only, no item
