"""Per-reply ETG cost grounded in real model price + admin pricing editor."""
import uuid

import pytest
from sqlalchemy import select

import app.api.webhooks as wh
from app.core.security import create_access_token
from app.db.models import AiModel, ModelProvider, User, UserRole


def _model(model_id, *, in_cost, out_cost, enabled=True):
    return AiModel(id=uuid.uuid4(), model_id=model_id, display_name=model_id,
                   provider=ModelProvider.openai, etg_cost_per_1k_input=in_cost,
                   etg_cost_per_1k_output=out_cost, is_enabled=enabled)


# ── cost computation (webhooks._reply_cost) ──────────────────────────────────

@pytest.mark.asyncio
async def test_cost_scales_with_model_price(db, mock_redis):
    db.add(_model("cheap/mini", in_cost=1, out_cost=2))
    db.add(_model("pricey/giant", in_cost=15, out_cost=60))
    await db.flush()
    cheap = await wh._reply_cost(db, mock_redis, "cheap/mini", 800, 400)
    pricey = await wh._reply_cost(db, mock_redis, "pricey/giant", 800, 400)
    assert cheap == 2            # ceil(800*1/1000)=1 + ceil(400*2/1000)=1
    assert pricey == 36          # ceil(800*15/1000)=12 + ceil(400*60/1000)=24
    assert pricey > cheap        # the whole point: expensive models cost more


@pytest.mark.asyncio
async def test_free_model_is_zero_rated(db, mock_redis):
    assert await wh._reply_cost(db, mock_redis, "meta-llama/llama-3.1-8b:free", 5000, 5000) == 0


@pytest.mark.asyncio
async def test_uncatalogued_model_uses_defaults(db, mock_redis):
    # not in the AiModel table → falls back to (1,2) defaults
    cost = await wh._reply_cost(db, mock_redis, "unknown/model", 1000, 1000)
    assert cost == 3             # ceil(1000*1/1000)=1 + ceil(1000*2/1000)=2


@pytest.mark.asyncio
async def test_paid_reply_is_at_least_one(db, mock_redis):
    db.add(_model("cheap/mini", in_cost=1, out_cost=2))
    await db.flush()
    assert await wh._reply_cost(db, mock_redis, "cheap/mini", 0, 0) == 1


# ── admin pricing editor ─────────────────────────────────────────────────────

@pytest.fixture
async def admin(db):
    u = User(id=uuid.uuid4(), email="a@x.com", telegram_id=959519454,
             is_admin=True, role=UserRole.admin, is_active=True)
    db.add(u)
    await db.flush()
    return u


@pytest.fixture
def admin_hdr(admin):
    return {"Authorization": f"Bearer {create_access_token(admin.id, role='admin')}"}


@pytest.mark.asyncio
async def test_list_model_pricing(client, db, admin, admin_hdr):
    db.add(_model("openai/gpt-4o-mini", in_cost=1, out_cost=2))
    await db.flush()
    r = await client.get("/api/v1/admin/models/pricing", headers=admin_hdr)
    assert r.status_code == 200, r.text
    row = next(m for m in r.json() if m["model_id"] == "openai/gpt-4o-mini")
    assert row["etg_cost_per_1k_input"] == 1 and row["etg_cost_per_1k_output"] == 2


@pytest.mark.asyncio
async def test_set_model_pricing(client, db, admin, admin_hdr, mock_redis):
    db.add(_model("openai/gpt-4o-mini", in_cost=99, out_cost=99))
    await db.flush()
    r = await client.patch("/api/v1/admin/models/pricing",
                           json={"model_id": "openai/gpt-4o-mini",
                                 "etg_cost_per_1k_input": 3, "etg_cost_per_1k_output": 6},
                           headers=admin_hdr)
    assert r.status_code == 200, r.text
    assert r.json()["etg_cost_per_1k_input"] == 3 and r.json()["etg_cost_per_1k_output"] == 6
    row = (await db.execute(select(AiModel).where(AiModel.model_id == "openai/gpt-4o-mini"))).scalar_one()
    assert row.etg_cost_per_1k_input == 3
    mock_redis.delete.assert_awaited()        # webhook price cache busted


@pytest.mark.asyncio
async def test_set_pricing_rejects_negative(client, db, admin, admin_hdr):
    db.add(_model("openai/gpt-4o-mini", in_cost=1, out_cost=2))
    await db.flush()
    r = await client.patch("/api/v1/admin/models/pricing",
                           json={"model_id": "openai/gpt-4o-mini", "etg_cost_per_1k_input": -5},
                           headers=admin_hdr)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_pricing_unknown_model_404(client, db, admin, admin_hdr):
    r = await client.patch("/api/v1/admin/models/pricing",
                           json={"model_id": "does/not-exist", "etg_cost_per_1k_input": 1},
                           headers=admin_hdr)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_pricing_requires_admin(client, db):
    plain = User(id=uuid.uuid4(), role=UserRole.owner, is_active=True)
    db.add(plain)
    await db.flush()
    hdr = {"Authorization": f"Bearer {create_access_token(plain.id, role='owner')}"}
    assert (await client.get("/api/v1/admin/models/pricing", headers=hdr)).status_code in (401, 403)
