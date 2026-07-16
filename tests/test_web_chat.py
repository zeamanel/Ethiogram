"""Public website support chat: stateless AI answer + rate-limited endpoint."""
import uuid

import pytest

import app.services.web_chat_service as wcs
from app.db.models import Business, KnowledgeItem, KnowledgeItemType, User


def _stub_llm(monkeypatch, reply="We're open Mon-Sat, 9am-6pm.", *, boom=False):
    async def _exec(*a, **k):
        if boom:
            raise RuntimeError("provider down")
        return reply, {"input_tokens": 1, "output_tokens": 1}, "test/model"
    monkeypatch.setattr(wcs.model_router, "execute_with_fallback", _exec)


async def _biz(db, owner_id=None):
    owner = User(id=owner_id or uuid.uuid4(), telegram_id=8181, is_active=True)
    db.add(owner)
    biz = Business(id=uuid.uuid4(), owner_id=owner.id, name="Selam Salon",
                   slug=f"s-{uuid.uuid4().hex[:6]}", address="Bole Rd")
    db.add(biz)
    await db.flush()
    return biz


# ── service ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_answer_uses_catalog_and_llm(db, monkeypatch):
    biz = await _biz(db)
    db.add(KnowledgeItem(business_id=biz.id, item_type=KnowledgeItemType.service,
                         title="Haircut", data={"price": "300 ETB"}, is_active=True))
    await db.flush()
    seen = {}

    async def _exec(messages, system_prompt="", **k):
        seen["system"] = system_prompt
        seen["messages"] = messages
        return "A haircut is 300 ETB.", {}, "m"
    monkeypatch.setattr(wcs.model_router, "execute_with_fallback", _exec)

    out = await wcs.answer(db, biz, "how much is a haircut?", [])
    assert out["source"] == "ai" and "300 ETB" in out["reply"]
    assert "Haircut" in seen["system"]                  # catalog fed into the prompt
    assert "Telegram" in seen["system"]                 # handoff instruction present
    assert seen["messages"][-1] == {"role": "user", "content": "how much is a haircut?"}


@pytest.mark.asyncio
async def test_answer_trims_history(db, monkeypatch):
    biz = await _biz(db)
    _stub_llm(monkeypatch)
    captured = {}

    async def _exec(messages, **k):
        captured["messages"] = messages
        return "ok", {}, "m"
    monkeypatch.setattr(wcs.model_router, "execute_with_fallback", _exec)

    history = [{"role": "user", "content": f"q{i}"} for i in range(20)]
    history.append({"role": "bogus", "content": "drop me"})
    await wcs.answer(db, biz, "latest", history)
    roles = [m["role"] for m in captured["messages"]]
    assert "bogus" not in roles                          # invalid role filtered
    assert len(captured["messages"]) <= 7                # capped history + current


@pytest.mark.asyncio
async def test_answer_falls_back_on_llm_error(db, monkeypatch):
    biz = await _biz(db)
    _stub_llm(monkeypatch, boom=True)
    out = await wcs.answer(db, biz, "hi", [])
    assert out["source"] == "fallback" and "Telegram" in out["reply"]


@pytest.mark.asyncio
async def test_answer_empty_message(db, monkeypatch):
    biz = await _biz(db)
    out = await wcs.answer(db, biz, "   ", [])
    assert out["source"] == "empty"


# ── endpoint ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_website_chat_endpoint(client, db, monkeypatch):
    import app.services.web_chat_service as svc
    biz = await _biz(db)

    async def _exec(*a, **k):
        return "We're open daily.", {}, "test/model"
    monkeypatch.setattr(svc.model_router, "execute_with_fallback", _exec)

    r = await client.post(f"/api/v1/miniapp/{biz.slug}/chat",
                          json={"message": "are you open?", "history": []})
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "We're open daily."


@pytest.mark.asyncio
async def test_website_chat_unknown_business_404(client, db):
    r = await client.post("/api/v1/miniapp/nope/chat", json={"message": "hi"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_website_chat_ip_rate_limited(client, db, mock_redis, monkeypatch):
    import app.api.miniapp as mp
    monkeypatch.setattr(mp, "_WEBCHAT_IP_PER_MIN", 2)
    counter = {"n": 0}

    async def _incr(key):
        counter["n"] += 1
        return counter["n"]
    mock_redis.incr = _incr   # ip throttle hits this first; biz cap also uses incr

    biz = await _biz(db)
    import app.services.web_chat_service as svc

    async def _exec(*a, **k):
        return "hi", {}, "m"
    monkeypatch.setattr(svc.model_router, "execute_with_fallback", _exec)

    url = f"/api/v1/miniapp/{biz.slug}/chat"
    # each request calls incr (ip) then incr (biz); with the shared counter the
    # 2nd request's ip incr is the 3rd call → over the per-min cap of 2.
    assert (await client.post(url, json={"message": "a"})).status_code == 200
    assert (await client.post(url, json={"message": "b"})).status_code == 429
