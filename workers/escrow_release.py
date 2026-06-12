# workers/escrow_release.py
"""
Automatic escrow release worker.

After the dispute window (default 7 days), held ETG is released to the
creator's account. If a dispute was opened, the escrow stays in 'disputed'
state until an admin resolves it manually.

Run daily via Cloud Scheduler:
    python -m workers.escrow_release
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.db.models import (
    Agent,
    AgentUnlock,
    CreatorProfile,
    EscrowStatus,
    EtgTransaction,
    TokenEscrow,
    TokenWallet,
)
from app.db.session import connect_db, connect_redis, disconnect_db, disconnect_redis, get_db_context

configure_logging()
logger = get_logger(__name__)

_CREATOR_REVENUE_SHARE = 0.70   # creator gets 70%
_PLATFORM_SHARE = 0.30          # platform keeps 30%


async def run() -> None:
    await connect_db()
    await connect_redis()
    try:
        await _release_matured_escrows()
    finally:
        await disconnect_db()
        await disconnect_redis()


async def _release_matured_escrows() -> None:
    now = datetime.now(timezone.utc)
    stats = {"released": 0, "disputed": 0, "skipped": 0}

    async with get_db_context() as db:
        result = await db.execute(
            select(TokenEscrow)
            .where(
                TokenEscrow.status == EscrowStatus.holding,
                TokenEscrow.release_at <= now,
            )
            .order_by(TokenEscrow.release_at.asc())
        )
        escrows = result.scalars().all()

    for escrow in escrows:
        if escrow.status != EscrowStatus.holding:
            stats["skipped"] += 1
            continue

        async with get_db_context() as db:
            try:
                released = await _release_escrow(escrow, db)
                if released:
                    stats["released"] += 1
                else:
                    stats["disputed"] += 1
            except Exception as exc:
                logger.error(
                    "Escrow release failed",
                    escrow_id=str(escrow.id),
                    error=str(exc),
                )

    logger.info("Escrow release worker complete", **stats)


async def _release_escrow(escrow: TokenEscrow, db: AsyncSession) -> bool:
    """
    Release escrow: move ETG from buyer's escrow balance,
    pay creator 70%, retain 30% for platform.
    Returns True if released, False if in dispute.
    """
    # Re-fetch with lock to avoid double-release race
    result = await db.execute(
        select(TokenEscrow)
        .where(TokenEscrow.id == escrow.id)
        .with_for_update()
    )
    escrow = result.scalar_one_or_none()
    if escrow is None or escrow.status != EscrowStatus.holding:
        return False

    if escrow.dispute_reason:
        logger.info("Escrow in dispute, skipping auto-release", escrow_id=str(escrow.id))
        return False

    # Find the AgentUnlock linked to this escrow
    unlock_result = await db.execute(
        select(AgentUnlock).where(AgentUnlock.escrow_id == escrow.id)
    )
    unlock = unlock_result.scalar_one_or_none()

    creator_etg = int(escrow.amount * _CREATOR_REVENUE_SHARE)
    platform_etg = escrow.amount - creator_etg

    # 1. Return buyer's escrow_balance (it was already deducted from balance at unlock time)
    buyer_wallet_result = await db.execute(
        select(TokenWallet).where(TokenWallet.id == escrow.wallet_id)
    )
    buyer_wallet = buyer_wallet_result.scalar_one_or_none()
    if buyer_wallet:
        buyer_wallet.escrow_balance = max(0, buyer_wallet.escrow_balance - escrow.amount)

    # 2. Pay creator
    if unlock:
        agent_result = await db.execute(
            select(Agent).where(Agent.id == unlock.agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if agent:
            creator_result = await db.execute(
                select(CreatorProfile).where(CreatorProfile.id == agent.creator_id)
            )
            creator_profile = creator_result.scalar_one_or_none()

            if creator_profile:
                creator_wallet_result = await db.execute(
                    select(TokenWallet)
                    .join(TokenWallet.business)
                    .where(TokenWallet.business.has(owner_id=creator_profile.user_id))
                    .limit(1)
                )
                creator_wallet = creator_wallet_result.scalar_one_or_none()

                if creator_wallet:
                    balance_before = creator_wallet.balance
                    creator_wallet.balance += creator_etg
                    creator_wallet.lifetime_recharged += creator_etg

                    db.add(EtgTransaction(
                        wallet_id=creator_wallet.id,
                        amount=creator_etg,
                        balance_before=balance_before,
                        balance_after=creator_wallet.balance,
                        transaction_type="creator_revenue",
                        description=f"Agent unlock revenue: {agent.name}",
                        reference_type="escrow_release",
                        reference_id=str(escrow.id),
                    ))

                    # Update creator stats
                    creator_profile.total_revenue_etg += creator_etg
                    creator_profile.total_unlocks += 1
                    agent.total_unlocks += 1

    # 3. Mark escrow released
    escrow.status = EscrowStatus.released
    escrow.released_at = datetime.now(timezone.utc)

    logger.info(
        "Escrow released",
        escrow_id=str(escrow.id),
        amount=escrow.amount,
        creator_etg=creator_etg,
        platform_etg=platform_etg,
    )
    return True


if __name__ == "__main__":
    asyncio.run(run())
