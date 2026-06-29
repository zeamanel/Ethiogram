"""A bot in 'grace' (ran out of ETG) must self-heal once the wallet is recharged."""
import uuid

import pytest

import app.api.webhooks as wh
from app.db.models import BotStatus, Business, TokenWallet


class _Bot:
    def __init__(self, business_id, status):
        self.id = uuid.uuid4()
        self.business_id = business_id
        self.status = status
        self.grace_period_started_at = "set"


async def _biz(db):
    biz = Business(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="B", slug=f"b-{uuid.uuid4().hex[:6]}")
    db.add(biz)
    await db.flush()
    return biz


@pytest.mark.asyncio
async def test_grace_with_empty_wallet_stays_blocked(db, mock_redis):
    biz = await _biz(db)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=0))
    await db.flush()
    bot = _Bot(biz.id, BotStatus.grace)
    assert await wh._bot_should_block(bot, db, mock_redis) is True
    assert bot.status == BotStatus.grace                       # unchanged


@pytest.mark.asyncio
async def test_grace_reactivates_when_recharged(db, mock_redis):
    biz = await _biz(db)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=500))   # topped up
    await db.flush()
    bot = _Bot(biz.id, BotStatus.grace)
    blocked = await wh._bot_should_block(bot, db, mock_redis)
    assert blocked is False                                    # let through
    assert bot.status == BotStatus.active                      # self-healed
    assert bot.grace_period_started_at is None
    mock_redis.delete.assert_awaited()                         # caches busted


@pytest.mark.asyncio
async def test_paused_bot_stays_blocked_even_with_balance(db, mock_redis):
    biz = await _biz(db)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=biz.id, balance=999))
    await db.flush()
    bot = _Bot(biz.id, BotStatus.paused)                       # admin pause, not grace
    assert await wh._bot_should_block(bot, db, mock_redis) is True
    assert bot.status == BotStatus.paused


@pytest.mark.asyncio
async def test_active_bot_passes(db, mock_redis):
    biz = await _biz(db)
    bot = _Bot(biz.id, BotStatus.active)
    assert await wh._bot_should_block(bot, db, mock_redis) is False
