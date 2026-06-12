# tests/test_metering.py
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import InsufficientBalanceError
from app.core.metering import MeteringService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def metering():
    return MeteringService()


@pytest.fixture
def business_id():
    return str(uuid.uuid4())


def _db_returning_scalar(value):
    """Mock AsyncSession whose execute().scalar_one_or_none() returns value."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    db.execute = AsyncMock(return_value=result)
    return db


class TestGetBalance:
    async def test_returns_cached_balance(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = b"500"
        balance = await metering.get_balance(business_id, mock_redis, db=AsyncMock())
        assert balance == 500
        mock_redis.get.assert_called_once()

    async def test_falls_back_to_db_on_cache_miss(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = None
        db = _db_returning_scalar(999)
        balance = await metering.get_balance(business_id, mock_redis, db=db)
        assert balance == 999
        mock_redis.setex.assert_called_once()  # re-warms cache

    async def test_missing_wallet_returns_zero(self, metering, mock_redis, business_id):
        mock_redis.get.return_value = None
        db = _db_returning_scalar(None)
        balance = await metering.get_balance(business_id, mock_redis, db=db)
        assert balance == 0

    async def test_bust_cache_deletes_key(self, metering, mock_redis, business_id):
        await metering.bust_balance_cache(business_id, mock_redis)
        mock_redis.delete.assert_called_once()


class TestPreFlightCheck:
    async def test_sufficient_balance_passes(self, metering, mock_redis, business_id):
        # balance lookup then pricing lookup both hit cache
        mock_redis.get.side_effect = [b"1000", b"2"]
        balance = await metering.pre_flight_check(
            business_id, "ai_reply", mock_redis, db=AsyncMock()
        )
        assert balance == 1000

    async def test_zero_balance_raises(self, metering, mock_redis, business_id):
        mock_redis.get.side_effect = [b"0", b"2"]
        with pytest.raises(InsufficientBalanceError):
            await metering.pre_flight_check(
                business_id, "ai_reply", mock_redis, db=AsyncMock()
            )

    async def test_insufficient_balance_raises(self, metering, mock_redis, business_id):
        mock_redis.get.side_effect = [b"1", b"2"]
        with pytest.raises(InsufficientBalanceError):
            await metering.pre_flight_check(
                business_id, "ai_reply", mock_redis, db=AsyncMock()
            )

    async def test_exact_balance_passes(self, metering, mock_redis, business_id):
        mock_redis.get.side_effect = [b"2", b"2"]
        balance = await metering.pre_flight_check(
            business_id, "ai_reply", mock_redis, db=AsyncMock()
        )
        assert balance == 2


class TestGetActionCost:
    async def test_returns_cached_cost(self, metering, mock_redis):
        mock_redis.get.return_value = b"2"
        cost = await metering.get_action_cost("ai_reply", mock_redis, db=AsyncMock())
        assert cost == 2

    async def test_db_pricing_used_on_cache_miss(self, metering, mock_redis):
        mock_redis.get.return_value = None
        db = _db_returning_scalar(7)
        cost = await metering.get_action_cost("custom_action", mock_redis, db=db)
        assert cost == 7
        mock_redis.setex.assert_called_once()

    async def test_default_cost_for_unknown_action(self, metering, mock_redis):
        mock_redis.get.return_value = None
        db = _db_returning_scalar(None)
        cost = await metering.get_action_cost("totally_unknown_action", mock_redis, db=db)
        assert cost == 1  # _DEFAULT_COSTS fallback
