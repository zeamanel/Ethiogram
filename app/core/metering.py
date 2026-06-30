# app/core/metering.py
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InsufficientBalanceError
from app.core.logging import get_logger
from app.db.models import (
    AlertType,
    BotStatus,
    Bot,
    EtgTransaction,
    ModelTier,
    TokenWallet,
    UsageEvent,
    UsagePricing,
    WalletAlert,
)

logger = get_logger(__name__)

# Redis cache TTLs
_BALANCE_TTL = 60         # seconds
_PRICING_TTL = 300        # pricing changes rarely
_ALERT_DEDUP_TTL = 3600   # 1 hour between same-level alerts

# Hardcoded fallback ETG costs if DB pricing table is empty
_DEFAULT_COSTS: dict[str, int] = {
    "ai_reply": 2,
    "rag_search": 1,
    "ocr_page": 3,
    "embedding": 1,
    "booking": 2,
    "order_assist": 2,
    "image_analysis": 4,
}

_ALERT_THRESHOLDS = {
    AlertType.low: 500,
    AlertType.critical: 100,
    AlertType.zero: 0,
}


class MeteringService:
    """
    Central ETG token metering engine.

    Responsibilities:
    - Pre-flight balance check before expensive AI operations
    - Post-processing charge: deduct ETG, write EtgTransaction + UsageEvent
    - Redis-cached balance for sub-millisecond reads on every message
    - Wallet alert state machine: low → critical → zero (Redis-deduped)
    - Bot status transitions on zero balance (active → grace)
    """

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_balance(self, business_id: str | uuid.UUID, redis, db: AsyncSession) -> int:
        """Return current ETG balance. Redis-cached, falls back to DB."""
        key = _balance_key(business_id)
        cached = await redis.get(key)
        if cached is not None:
            return int(cached)

        result = await db.execute(
            select(TokenWallet.balance).where(
                TokenWallet.business_id == str(business_id)
            )
        )
        row = result.scalar_one_or_none()
        balance = int(row) if row is not None else 0
        await redis.setex(key, _BALANCE_TTL, balance)
        return balance

    async def bust_balance_cache(self, business_id: str | uuid.UUID, redis) -> None:
        await redis.delete(_balance_key(business_id))

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    async def pre_flight_check(
        self,
        business_id: str | uuid.UUID,
        action_type: str,
        redis,
        db: AsyncSession,
    ) -> int:
        """
        Raises InsufficientBalanceError if wallet cannot cover `action_type`.
        Returns current balance on success.
        """
        balance = await self.get_balance(business_id, redis, db)
        cost = await self.get_action_cost(action_type, redis, db)
        if balance < cost:
            raise InsufficientBalanceError(required=cost, available=balance)
        return balance

    # ------------------------------------------------------------------
    # Charge
    # ------------------------------------------------------------------

    async def charge(
        self,
        business_id: str | uuid.UUID,
        action_type: str,
        redis,
        db: AsyncSession,
        model_tier: Optional[ModelTier] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        bot_id: Optional[str | uuid.UUID] = None,
        conversation_id: Optional[str | uuid.UUID] = None,
        reference_id: Optional[str] = None,
    ) -> int:
        """
        Deduct ETG for an action. Writes EtgTransaction + UsageEvent.
        Returns the amount charged.
        """
        base_cost = await self.get_action_cost(action_type, redis, db)

        # Token-based surcharge for AI calls
        token_surcharge = 0
        if input_tokens or output_tokens:
            token_surcharge = max(0, (input_tokens // 1000) + (output_tokens // 1000) * 2)

        etg = base_cost + token_surcharge

        result = await db.execute(
            select(TokenWallet).where(TokenWallet.business_id == str(business_id))
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            logger.warning("Wallet not found during charge", business_id=str(business_id))
            return 0

        balance_before = wallet.balance
        wallet.balance = max(0, wallet.balance - etg)
        wallet.lifetime_spent += etg
        wallet.current_month_spend += etg

        tx = EtgTransaction(
            wallet_id=wallet.id,
            amount=-etg,
            balance_before=balance_before,
            balance_after=wallet.balance,
            transaction_type="usage",
            description=f"{action_type} charge",
            reference_type=action_type,
            reference_id=str(reference_id) if reference_id else str(conversation_id) if conversation_id else None,
        )
        db.add(tx)

        usage = UsageEvent(
            business_id=str(business_id),
            bot_id=str(bot_id) if bot_id else None,
            conversation_id=str(conversation_id) if conversation_id else None,
            action_type=action_type,
            model_tier=model_tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            etg_charged=etg,
        )
        db.add(usage)

        # Update Redis cache optimistically
        await redis.setex(_balance_key(business_id), _BALANCE_TTL, wallet.balance)

        logger.etg_charged(
            amount=etg,
            action_type=action_type,
            business_id=str(business_id),
            balance_after=wallet.balance,
        )
        return etg

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------

    async def get_action_cost(
        self, action_type: str, redis, db: AsyncSession
    ) -> int:
        """Return ETG cost for an action. Redis-cached pricing table."""
        cache_key = f"pricing:{action_type}"
        cached = await redis.get(cache_key)
        if cached is not None:
            return int(cached)

        result = await db.execute(
            select(UsagePricing.etg_cost).where(
                UsagePricing.action_type == action_type,
                UsagePricing.is_active.is_(True),
            )
        )
        row = result.scalar_one_or_none()
        cost = int(row) if row is not None else _DEFAULT_COSTS.get(action_type, 1)
        await redis.setex(cache_key, _PRICING_TTL, cost)
        return cost

    async def invalidate_pricing_cache(self, redis) -> None:
        """Call after admin updates pricing table."""
        keys = await redis.keys("pricing:*")
        if keys:
            await redis.delete(*keys)

    # ------------------------------------------------------------------
    # Wallet alerts
    # ------------------------------------------------------------------

    async def trigger_alerts_if_needed(
        self,
        business_id: str | uuid.UUID,
        new_balance: int,
        bot: Optional[Bot],
        redis,
        db: AsyncSession,
    ) -> None:
        """
        Check balance thresholds and log wallet alerts.
        Redis dedup prevents repeat alerts within _ALERT_DEDUP_TTL seconds.
        Transitions bot to grace status on zero balance.
        """
        dedup_key = f"wallet:alert_sent:{business_id}"
        last_sent = await redis.get(dedup_key)

        if new_balance <= _ALERT_THRESHOLDS[AlertType.zero] and last_sent != "zero":
            await self._log_alert(business_id, AlertType.zero, new_balance, db)
            await redis.setex(dedup_key, 86400, "zero")
            if bot and bot.status == BotStatus.active:
                bot.status = BotStatus.grace
                bot.grace_period_started_at = datetime.now(timezone.utc)
                db.add(bot)
            logger.warning(
                "Wallet reached zero",
                business_id=str(business_id),
                balance=new_balance,
            )

        elif new_balance <= _ALERT_THRESHOLDS[AlertType.critical] and last_sent not in ("critical", "zero"):
            await self._log_alert(business_id, AlertType.critical, new_balance, db)
            await redis.setex(dedup_key, _ALERT_DEDUP_TTL, "critical")

        elif new_balance <= _ALERT_THRESHOLDS[AlertType.low] and last_sent not in ("low", "critical", "zero"):
            await self._log_alert(business_id, AlertType.low, new_balance, db)
            await redis.setex(dedup_key, _ALERT_DEDUP_TTL, "low")

    async def _log_alert(
        self,
        business_id: str | uuid.UUID,
        alert_type: AlertType,
        balance: int,
        db: AsyncSession,
    ) -> None:
        result = await db.execute(
            select(TokenWallet).where(TokenWallet.business_id == str(business_id))
        )
        wallet = result.scalar_one_or_none()
        if wallet:
            db.add(WalletAlert(
                wallet_id=wallet.id,
                alert_type=alert_type,
                balance_at_alert=balance,
                sent_via=["dashboard"],
            ))

    # ------------------------------------------------------------------
    # Credits / grants
    # ------------------------------------------------------------------

    async def credit(
        self,
        business_id: str | uuid.UUID,
        amount: int,
        description: str,
        redis,
        db: AsyncSession,
        reference_type: str = "manual",
        reference_id: Optional[str] = None,
        admin_id: Optional[str | uuid.UUID] = None,
    ) -> int:
        """
        Add ETG to a wallet (bonus, recharge, admin grant).
        Returns new balance.
        """
        bid = business_id if isinstance(business_id, uuid.UUID) else uuid.UUID(str(business_id))
        result = await db.execute(
            select(TokenWallet).where(TokenWallet.business_id == bid)
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = TokenWallet(business_id=bid, balance=0, lifetime_recharged=0)
            db.add(wallet)
            await db.flush()

        balance_before = wallet.balance
        wallet.balance += amount
        if reference_type in ("recharge", "bonus", "admin_grant"):
            wallet.lifetime_recharged += amount

        tx = EtgTransaction(
            wallet_id=wallet.id,
            amount=amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            transaction_type=reference_type,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
            admin_id=str(admin_id) if admin_id else None,
        )
        db.add(tx)

        await redis.setex(_balance_key(business_id), _BALANCE_TTL, wallet.balance)
        logger.info(
            "ETG credited",
            amount=amount,
            business_id=str(business_id),
            balance_after=wallet.balance,
            reason=description,
        )
        return wallet.balance


def _balance_key(business_id) -> str:
    return f"wallet:balance:{business_id}"


metering_service = MeteringService()
