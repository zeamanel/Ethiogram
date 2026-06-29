"""The bot sends a product's photo when the conversation references it."""
import uuid
from types import SimpleNamespace

import pytest

import app.api.webhooks as wh
from app.db.models import Business, KnowledgeItem, KnowledgeItemType, User


@pytest.fixture
def photos(monkeypatch):
    sent = []

    async def _send_photo(token, chat_id, photo_url, caption=None, **kw):
        sent.append({"url": photo_url, "caption": caption})
        return {}
    monkeypatch.setattr(wh.telegram_service, "send_photo", _send_photo)
    return sent


def _env(text):
    return SimpleNamespace(customer_id="999", customer_name="C", text=text, chat_type="private")


async def _biz(db):
    owner = User(id=uuid.uuid4(), telegram_id=5, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="Shop", slug=f"s-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    return biz


def _bot(biz):
    return SimpleNamespace(id=uuid.uuid4(), business_id=biz.id)


async def _product(db, biz_id, title, *, image=True, price=None):
    data = {}
    if image:
        data["image_url"] = f"https://img/{title.replace(' ', '_')}.jpg"
    if price:
        data["price"] = price
    db.add(KnowledgeItem(business_id=biz_id, item_type=KnowledgeItemType.product,
                         title=title, data=data or None, is_active=True))
    await db.flush()


@pytest.mark.asyncio
async def test_sends_image_when_product_mentioned(db, photos):
    biz = await _biz(db)
    await _product(db, biz.id, "Blue Dress", price="1200 ETB")
    n = await wh._maybe_send_product_images(
        _env("do you have the blue dress?"), _bot(biz), "tok",
        "Yes! Our Blue Dress is 1200 ETB.", db)
    assert n == 1
    assert photos[0]["url"].endswith("Blue_Dress.jpg")
    assert "Blue Dress" in photos[0]["caption"] and "1200 ETB" in photos[0]["caption"]


@pytest.mark.asyncio
async def test_no_image_when_not_mentioned(db, photos):
    biz = await _biz(db)
    await _product(db, biz.id, "Blue Dress")
    n = await wh._maybe_send_product_images(
        _env("what are your opening hours?"), _bot(biz), "tok",
        "We're open Mon-Sat, 9-6.", db)
    assert n == 0 and photos == []


@pytest.mark.asyncio
async def test_skips_product_without_image(db, photos):
    biz = await _biz(db)
    await _product(db, biz.id, "Red Hat", image=False)
    n = await wh._maybe_send_product_images(
        _env("tell me about the red hat"), _bot(biz), "tok", "The Red Hat is great.", db)
    assert n == 0 and photos == []


@pytest.mark.asyncio
async def test_prefers_specific_and_caps_at_two(db, photos):
    biz = await _biz(db)
    await _product(db, biz.id, "Dress")
    await _product(db, biz.id, "Blue Summer Dress")
    await _product(db, biz.id, "Red Dress")
    await _product(db, biz.id, "Green Dress")
    n = await wh._maybe_send_product_images(
        _env("show me the blue summer dress, red dress and green dress"), _bot(biz), "tok", "", db)
    assert n == 2                                   # capped
    # the most specific (longest) title is sent first
    assert "Blue Summer Dress" in photos[0]["caption"]
