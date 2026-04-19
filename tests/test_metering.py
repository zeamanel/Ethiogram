# tests/test_metering.py
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.core.metering import MeteringService
from app.core.exceptions import InsufficientBalanceError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def metering(mock_redis):
    return MeteringService(mock_redis)


@pytest.fixture
def business_id():
    return str(uuid.uuid4())


class TestGetBalance:
    async def test_returns_cached_balance(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = b"500"
        balance = await metering.get_balance(business_id, db=AsyncMock())
        assert balance == 500
        mock_redis.get.assert_called_once()

    async def test_falls_back_to_db_on_cache_miss(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = None
        db = AsyncMock()
        mock_wallet = MagicMock()
        mock_wallet.balance = 999
        db.scalar = AsyncMock(return_value=None)

        with patch("app.core.metering.select"):
            db.execute = AsyncMock(return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=mock_wallet)
            ))
            balance = await metering.get_balance(business_id, db=db)

        assert balance == 999


class TestPreFlightCheck:
    async def test_sufficient_balance_passes(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = b"1000"
        db = AsyncMock()
        # Should not raise
        await metering.pre_flight_check(business_id, cost=5, db=db)

    async def test_zero_balance_raises(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = b"0"
        db = AsyncMock()
        with pytest.raises(InsufficientBalanceError):
            await metering.pre_flight_check(business_id, cost=1, db=db)

    async def test_insufficient_balance_raises(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = b"3"
        db = AsyncMock()
        with pytest.raises(InsufficientBalanceError):
            await metering.pre_flight_check(business_id, cost=5, db=db)


class TestGetActionCost:
    async def test_returns_cached_cost(self, metering, mock_redis):
        mock_redis.get.return_value = b"2"
        cost = await metering.get_action_cost("ai_reply", db=AsyncMock())
        assert cost == 2

    async def test_default_cost_on_miss(self, metering, mock_redis):
        mock_redis.get.return_value = None
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)

        with patch("app.core.metering.select"):
            cost = await metering.get_action_cost("unknown_action", db=db)

        assert cost == 1  # default fallback
