# workers/trial_monitor.py
"""
Agent trial lifecycle monitor.

Responsibilities:
- Day 13 warning: notify business owner that trial expires in 2 days
- Day 14 critical: final warning
- Day 15+ expired: deactivate ChildAgent, notify owner

Run daily via Cloud Scheduler:
    python -m workers.trial_monitor
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.db.models import Agent, AgentTrial, Business, Bot, BotStatus, ChildAgent, User
from app.db.session import connect_db, connect_redis, disconnect_db, disconnect_redis, get_db_context
from app.services.telegram_service import telegram_service
from app.core.security import decrypt

configure_logging()
logger = get_logger(__name__)


async def run() -> None:
    await connect_db()
    await connect_redis()
    try:
        await _process_trials()
    finally:
        await disconnect_db()
        await disconnect_redis()


async def _process_trials() -> None:
    now = datetime.now(timezone.utc)

    async with get_db_context() as db:
        result = await db.execute(
            select(AgentTrial)
            .where(AgentTrial.is_converted.is_(False))
            .order_by(AgentTrial.expires_at.asc())
        )
        trials = result.scalars().all()

    stats = {"warned": 0, "critical": 0, "expired": 0, "skipped": 0}

    for trial in trials:
        days_left = (trial.expires_at - now).days

        if days_left > 2:
            stats["skipped"] += 1
            continue

        async with get_db_context() as db:
            try:
                if days_left <= 0 and trial.expired_notified_at is None:
                    await _handle_expired(trial, db)
                    stats["expired"] += 1
                elif days_left == 1 and trial.critical_sent_at is None:
                    await _handle_critical(trial, db)
                    stats["critical"] += 1
                elif days_left == 2 and trial.warning_sent_at is None:
                    await _handle_warning(trial, db)
                    stats["warned"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as exc:
                logger.error(
                    "Trial processing error",
                    trial_id=str(trial.id),
                    error=str(exc),
                )

    logger.info("Trial monitor complete", **stats)


async def _handle_warning(trial: AgentTrial, db: AsyncSession) -> None:
    agent_name = await _get_agent_name(trial.agent_id, db)
    message = (
        f"⏰ <b>Trial ending soon</b>\n\n"
        f"Your free trial of <b>{agent_name}</b> expires in 2 days.\n"
        f"Unlock it now to keep your AI assistant active."
    )
    await _notify_business(trial.business_id, message, db)
    trial.warning_sent_at = datetime.now(timezone.utc)
    db.add(trial)
    logger.info("Trial warning sent", trial_id=str(trial.id), agent_id=str(trial.agent_id))


async def _handle_critical(trial: AgentTrial, db: AsyncSession) -> None:
    agent_name = await _get_agent_name(trial.agent_id, db)
    message = (
        f"🚨 <b>Last day of trial</b>\n\n"
        f"Your free trial of <b>{agent_name}</b> expires tomorrow.\n"
        f"Unlock now to avoid interruption to your customers."
    )
    await _notify_business(trial.business_id, message, db)
    trial.critical_sent_at = datetime.now(timezone.utc)
    db.add(trial)
    logger.info("Trial critical sent", trial_id=str(trial.id))


async def _handle_expired(trial: AgentTrial, db: AsyncSession) -> None:
    agent_name = await _get_agent_name(trial.agent_id, db)

    # Deactivate the ChildAgent
    if trial.child_agent_id:
        child_result = await db.execute(
            select(ChildAgent).where(ChildAgent.id == trial.child_agent_id)
        )
        child = child_result.scalar_one_or_none()
        if child:
            child.is_active = False
            db.add(child)

    message = (
        f"❌ <b>Trial expired</b>\n\n"
        f"Your free trial of <b>{agent_name}</b> has ended.\n"
        f"The agent has been deactivated. Unlock it to reactivate."
    )
    await _notify_business(trial.business_id, message, db)
    trial.expired_notified_at = datetime.now(timezone.utc)
    db.add(trial)
    logger.info("Trial expired + deactivated", trial_id=str(trial.id))


async def _get_agent_name(agent_id, db: AsyncSession) -> str:
    result = await db.execute(select(Agent.name).where(Agent.id == agent_id))
    name = result.scalar_one_or_none()
    return name or "Agent"


async def _notify_business(business_id, message: str, db: AsyncSession) -> None:
    """Send notification to the first active bot of the business owner."""
    bot_result = await db.execute(
        select(Bot)
        .join(Bot.business)
        .where(
            Bot.business_id == business_id,
            Bot.status == BotStatus.active,
        )
        .limit(1)
    )
    bot = bot_result.scalar_one_or_none()
    if bot is None:
        return

    biz_result = await db.execute(
        select(Business).where(Business.id == business_id)
    )
    biz = biz_result.scalar_one_or_none()
    if biz is None or biz.owner is None or not biz.owner.telegram_id:
        return

    try:
        raw_token = decrypt(bot.encrypted_token)
        await telegram_service.send_message(raw_token, biz.owner.telegram_id, message)
    except Exception as exc:
        logger.warning("Trial notification failed", business_id=str(business_id), error=str(exc))


if __name__ == "__main__":
    asyncio.run(run())
