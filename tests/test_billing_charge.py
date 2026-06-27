"""Unit tests for the billing charge logic: _decide_payer, _apply_monthly_reset,
and _charge_etg across the three policies + per-user free-tier cap."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.api.webhooks import _apply_monthly_reset, _charge_etg, _decide_payer
from app.db.models import (
    Bot,
    BotStatus,
    Business,
    Conversation,
    EtgTransaction,
    TokenWallet,
    UsageEvent,
    User,
    UserRole,
)


class _Biz:
    """Lightweight stand-in for Business (only the billing fields _decide_payer reads)."""
    def __init__(self, policy="business_pays", limit=None, action="block", price=0, markup=0):
        self.billing_policy = policy
        self.per_user_monthly_limit = limit
        self.per_user_limit_action = action
        self.service_price = price
        self.business_markup = markup


class _Conv:
    def __init__(self, used=0, bal=0):
        self.monthly_etg_used = used
        self.etg_balance = bal
        self.monthly_reset_at = None


# ── _decide_payer ─────────────────────────────────────────────────────────────

def test_business_pays_default():
    assert _decide_payer(_Biz(), _Conv()) == ("business", None)


def test_business_pays_under_limit():
    assert _decide_payer(_Biz(limit=100), _Conv(used=50)) == ("business", None)


def test_business_pays_limit_reached_blocks():
    assert _decide_payer(_Biz(limit=100, action="block"), _Conv(used=100)) == ("business", "limit")


def test_business_pays_limit_switches_to_user_when_funded():
    biz = _Biz(limit=100, action="user_pays", price=5)
    assert _decide_payer(biz, _Conv(used=120, bal=10)) == ("user", None)


def test_business_pays_limit_switches_but_user_broke():
    biz = _Biz(limit=100, action="user_pays", price=5)
    assert _decide_payer(biz, _Conv(used=120, bal=2)) == ("user", "recharge")


def test_user_pays_funded_vs_broke():
    biz = _Biz(policy="user_pays", price=5)
    assert _decide_payer(biz, _Conv(bal=5)) == ("user", None)
    assert _decide_payer(biz, _Conv(bal=4)) == ("user", "recharge")


def test_both_requires_user_balance():
    biz = _Biz(policy="both", price=5)
    assert _decide_payer(biz, _Conv(bal=5)) == ("both", None)
    assert _decide_payer(biz, _Conv(bal=1)) == ("both", "recharge")


# ── _apply_monthly_reset ──────────────────────────────────────────────────────

def test_monthly_reset_rolls_over():
    c = _Conv(used=500)
    c.monthly_reset_at = datetime(2026, 5, 1, tzinfo=timezone.utc)   # last month
    _apply_monthly_reset(c, datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert c.monthly_etg_used == 0

    c2 = _Conv(used=500)
    c2.monthly_reset_at = datetime(2026, 6, 2, tzinfo=timezone.utc)  # same month
    _apply_monthly_reset(c2, datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert c2.monthly_etg_used == 500   # not reset


# ── _charge_etg (DB) ──────────────────────────────────────────────────────────

async def _setup(db, *, policy="business_pays", price=0, markup=0, wallet_balance=1000):
    uid = uuid.uuid4(); bid = uuid.uuid4()
    db.add(User(id=uid, role=UserRole.owner, is_active=True))
    biz = Business(id=bid, owner_id=uid, name="B", slug=f"b-{bid.hex[:6]}",
                   billing_policy=policy, service_price=price, business_markup=markup)
    db.add(biz)
    bot = Bot(id=uuid.uuid4(), business_id=bid, encrypted_token="x",
              token_hash=uuid.uuid4().hex, status=BotStatus.active)
    db.add(bot)
    db.add(TokenWallet(id=uuid.uuid4(), business_id=bid, balance=wallet_balance))
    conv = Conversation(id=uuid.uuid4(), business_id=bid, bot_id=bot.id,
                        customer_platform_id="tg-1", etg_balance=100, monthly_etg_used=0)
    db.add(conv)
    await db.flush()
    return biz, bot, conv


@pytest.mark.asyncio
async def test_charge_business_pays(db, mock_redis):
    biz, bot, conv = await _setup(db)
    cost = await _charge_etg(biz, conv, str(bot.id), "gpt", {"input_tokens": 0, "output_tokens": 0},
                             "business", mock_redis, db)
    wallet = (await db.execute(select(TokenWallet).where(TokenWallet.business_id == biz.id))).scalar_one()
    assert wallet.balance == 1000 - cost          # business wallet debited
    assert conv.monthly_etg_used == cost          # counts toward free-tier cap
    ue = (await db.execute(select(UsageEvent))).scalars().one()
    assert ue.payer == "business"


@pytest.mark.asyncio
async def test_charge_user_pays_credits_markup(db, mock_redis):
    biz, bot, conv = await _setup(db, policy="user_pays", price=5, markup=2)
    cost = await _charge_etg(biz, conv, str(bot.id), "gpt", {"input_tokens": 0, "output_tokens": 0},
                             "user", mock_redis, db)
    wallet = (await db.execute(select(TokenWallet).where(TokenWallet.business_id == biz.id))).scalar_one()
    assert conv.etg_balance == 100 - 5            # user charged the service price
    assert wallet.balance == 1000 + 2            # business credited the markup
    assert conv.monthly_etg_used == 0            # user-paid doesn't touch the free cap
    tx_types = {t.transaction_type for t in (await db.execute(select(EtgTransaction))).scalars().all()}
    assert "markup" in tx_types                  # markup transaction logged
    ue = (await db.execute(select(UsageEvent))).scalars().one()
    assert ue.payer == "user"


@pytest.mark.asyncio
async def test_charge_both_debits_business_and_user(db, mock_redis):
    biz, bot, conv = await _setup(db, policy="both", price=5, markup=2)
    cost = await _charge_etg(biz, conv, str(bot.id), "gpt", {"input_tokens": 0, "output_tokens": 0},
                             "both", mock_redis, db)
    wallet = (await db.execute(select(TokenWallet).where(TokenWallet.business_id == biz.id))).scalar_one()
    assert conv.etg_balance == 95                          # user pays price
    assert wallet.balance == 1000 - cost + 2              # business pays cost, earns markup
