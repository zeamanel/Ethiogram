# app/api/admin.py
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.metering import metering_service
from app.core.security import is_super_admin_telegram_id
from app.db.models import (
    AdminAuditLog,
    Agent,
    AgentStatus,
    Bot,
    BotStatus,
    Business,
    Notification,
    PlatformSetting,
    TokenWallet,
    User,
    UserRole,
    UsageEvent,
    Conversation,
)
from app.db.session import get_db, get_redis
from app.services.model_router import model_router

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ModelOverrideRequest(BaseModel):
    model_id: str

class BusinessOverrideRequest(BaseModel):
    business_id: str
    model_id: str

class FailoverChainRequest(BaseModel):
    chain: list[str]

class SuspendBotRequest(BaseModel):
    bot_id: uuid.UUID
    reason: str

class SuspendUserRequest(BaseModel):
    user_id: uuid.UUID
    reason: str

class WalletGrantRequest(BaseModel):
    business_id: uuid.UUID
    amount: int          # positive = grant, negative = deduct
    reason: str

class AgentReviewRequest(BaseModel):
    agent_id: uuid.UUID
    action: str          # "approve" | "reject"
    reason: Optional[str] = None

class AgentSuspendRequest(BaseModel):
    agent_id: uuid.UUID
    reason: str

class SettingUpdateRequest(BaseModel):
    key: str
    value: str
    description: Optional[str] = None

class BroadcastRequest(BaseModel):
    title: str
    body: str
    notification_type: str = "broadcast"
    target_role: Optional[str] = None   # None = all users


# ---------------------------------------------------------------------------
# AI Model control
# ---------------------------------------------------------------------------

@router.get("/models/status")
async def get_model_statuses(
    current_admin: CurrentAdmin,
    redis=Depends(get_redis),
) -> dict:
    statuses = await model_router.get_model_statuses()
    from app.db.session import get_redis as _get_redis
    global_override = await redis.get("admin:model:global_override")
    failover_raw = await redis.get("admin:model:failover_chain")
    import json
    failover = json.loads(failover_raw) if failover_raw else [
        settings.default_model_id, settings.fallback_model_id, settings.emergency_model_id
    ]
    return {
        "model_health": statuses,
        "global_override": global_override,
        "failover_chain": failover,
        "defaults": {
            "primary": settings.default_model_id,
            "fallback": settings.fallback_model_id,
            "emergency": settings.emergency_model_id,
        },
    }


@router.post("/models/global-override", status_code=204)
async def set_global_override(
    body: ModelOverrideRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    _require_super_admin(current_admin)
    await model_router.set_global_override(body.model_id)
    await _audit(current_admin, "set_global_model_override", "model", body.model_id,
                 new_value={"model_id": body.model_id}, db=db)


@router.delete("/models/global-override", status_code=204)
async def clear_global_override(
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    _require_super_admin(current_admin)
    await model_router.clear_global_override()
    await _audit(current_admin, "clear_global_model_override", "model", "global", db=db)


@router.post("/models/business-override", status_code=204)
async def set_business_override(
    body: BusinessOverrideRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    await model_router.set_business_override(body.business_id, body.model_id)
    await _audit(current_admin, "set_business_model_override", "business", body.business_id,
                 new_value={"model_id": body.model_id}, db=db)


@router.delete("/models/business-override/{business_id}", status_code=204)
async def clear_business_override(
    business_id: str,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    await model_router.clear_business_override(business_id)
    await _audit(current_admin, "clear_business_model_override", "business", business_id, db=db)


@router.post("/models/disable", status_code=204)
async def disable_model(
    body: ModelOverrideRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    _require_super_admin(current_admin)
    await model_router.disable_model(body.model_id)
    await _audit(current_admin, "disable_model", "model", body.model_id, db=db)


@router.post("/models/enable", status_code=204)
async def enable_model(
    body: ModelOverrideRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    await model_router.enable_model(body.model_id)
    await _audit(current_admin, "enable_model", "model", body.model_id, db=db)


@router.post("/models/failover-chain", status_code=204)
async def set_failover_chain(
    body: FailoverChainRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    _require_super_admin(current_admin)
    if len(body.chain) < 1:
        raise ValidationError("Failover chain must have at least one model")
    await model_router.set_failover_chain(body.chain)
    await _audit(current_admin, "set_failover_chain", "model", "chain",
                 new_value={"chain": body.chain}, db=db)


# ---------------------------------------------------------------------------
# Bot control
# ---------------------------------------------------------------------------

@router.post("/bots/suspend", status_code=204)
async def suspend_bot(
    body: SuspendBotRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Bot).where(Bot.id == body.bot_id))
    bot = result.scalar_one_or_none()
    if bot is None:
        raise NotFoundError("Bot", str(body.bot_id))

    old = {"status": bot.status.value}
    bot.status = BotStatus.suspended
    bot.suspended_at = datetime.now(timezone.utc)
    bot.suspension_reason = body.reason
    db.add(bot)

    await _audit(current_admin, "suspend_bot", "bot", str(body.bot_id),
                 old_value=old, new_value={"status": "suspended", "reason": body.reason}, db=db)
    logger.admin_action(str(current_admin.id), "suspend_bot", "bot", str(body.bot_id))


@router.post("/bots/restore", status_code=204)
async def restore_bot(
    body: SuspendBotRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Bot).where(Bot.id == body.bot_id))
    bot = result.scalar_one_or_none()
    if bot is None:
        raise NotFoundError("Bot", str(body.bot_id))

    old = {"status": bot.status.value}
    bot.status = BotStatus.active
    bot.suspended_at = None
    bot.suspension_reason = None
    db.add(bot)

    await _audit(current_admin, "restore_bot", "bot", str(body.bot_id),
                 old_value=old, new_value={"status": "active"}, db=db)


# ---------------------------------------------------------------------------
# User control
# ---------------------------------------------------------------------------

@router.post("/users/suspend", status_code=204)
async def suspend_user(
    body: SuspendUserRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    _require_super_admin(current_admin)
    result = await db.execute(select(User).where(User.id == body.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User", str(body.user_id))

    old = {"is_active": user.is_active}
    user.is_active = False
    user.suspended_at = datetime.now(timezone.utc)
    user.suspension_reason = body.reason
    db.add(user)

    await _audit(current_admin, "suspend_user", "user", str(body.user_id),
                 old_value=old, new_value={"is_active": False, "reason": body.reason}, db=db)


@router.post("/users/restore", status_code=204)
async def restore_user(
    body: SuspendUserRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    _require_super_admin(current_admin)
    result = await db.execute(select(User).where(User.id == body.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User", str(body.user_id))

    old = {"is_active": user.is_active}
    user.is_active = True
    user.suspended_at = None
    user.suspension_reason = None
    db.add(user)

    await _audit(current_admin, "restore_user", "user", str(body.user_id),
                 old_value=old, new_value={"is_active": True}, db=db)


# ---------------------------------------------------------------------------
# Wallet management
# ---------------------------------------------------------------------------

@router.post("/wallets/grant", status_code=204)
async def grant_etg(
    body: WalletGrantRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> None:
    _require_super_admin(current_admin)
    if body.amount == 0:
        raise ValidationError("Amount cannot be zero")

    new_balance = await metering_service.credit(
        business_id=body.business_id,
        amount=body.amount,
        description=f"Admin grant: {body.reason}",
        redis=redis,
        db=db,
        reference_type="admin_grant",
        admin_id=current_admin.id,
    )

    await _audit(current_admin, "wallet_grant", "wallet", str(body.business_id),
                 new_value={"amount": body.amount, "reason": body.reason,
                            "balance_after": new_balance}, db=db)


# ---------------------------------------------------------------------------
# Agent marketplace
# ---------------------------------------------------------------------------

@router.post("/agents/review", status_code=204)
async def review_agent(
    body: AgentReviewRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    if body.action not in ("approve", "reject"):
        raise ValidationError("action must be 'approve' or 'reject'")

    result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise NotFoundError("Agent", str(body.agent_id))

    old = {"status": agent.status.value}
    agent.status = AgentStatus.live if body.action == "approve" else AgentStatus.rejected
    agent.rejection_reason = body.reason if body.action == "reject" else None
    agent.reviewed_by_id = current_admin.id
    agent.reviewed_at = datetime.now(timezone.utc)
    db.add(agent)

    await _audit(current_admin, f"agent_{body.action}", "agent", str(body.agent_id),
                 old_value=old, new_value={"status": agent.status.value, "reason": body.reason}, db=db)


@router.post("/agents/suspend", status_code=204)
async def suspend_agent(
    body: AgentSuspendRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Agent).where(Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise NotFoundError("Agent", str(body.agent_id))

    old = {"status": agent.status.value}
    agent.status = AgentStatus.suspended
    agent.rejection_reason = body.reason
    db.add(agent)

    await _audit(current_admin, "suspend_agent", "agent", str(body.agent_id),
                 old_value=old, new_value={"status": "suspended", "reason": body.reason}, db=db)


# ---------------------------------------------------------------------------
# Platform settings
# ---------------------------------------------------------------------------

@router.get("/settings")
async def get_settings(
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(
        select(PlatformSetting).order_by(PlatformSetting.key)
    )
    settings_list = result.scalars().all()
    return [
        {
            "key": s.key,
            "value": s.value,
            "value_type": s.value_type,
            "description": s.description,
            "is_public": s.is_public,
            "updated_at": s.updated_at.isoformat(),
        }
        for s in settings_list
    ]


@router.put("/settings", status_code=204)
async def update_setting(
    body: SettingUpdateRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> None:
    _require_super_admin(current_admin)

    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == body.key)
    )
    setting = result.scalar_one_or_none()

    old_value = None
    if setting:
        old_value = {"value": setting.value}
        setting.value = body.value
        setting.updated_by_id = current_admin.id
        if body.description:
            setting.description = body.description
    else:
        setting = PlatformSetting(
            key=body.key,
            value=body.value,
            description=body.description,
            updated_by_id=current_admin.id,
        )
        db.add(setting)

    # Invalidate setting from Redis so it's re-read live
    await redis.delete(f"platform:setting:{body.key}")

    await _audit(current_admin, "update_setting", "platform_setting", body.key,
                 old_value=old_value, new_value={"value": body.value}, db=db)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def get_platform_metrics(
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    total_users = await db.scalar(select(func.count(User.id)))
    total_businesses = await db.scalar(select(func.count(Business.id)))
    total_bots = await db.scalar(select(func.count(Bot.id)))
    active_bots = await db.scalar(
        select(func.count(Bot.id)).where(Bot.status == BotStatus.active)
    )
    total_agents = await db.scalar(
        select(func.count(Agent.id)).where(Agent.status == AgentStatus.live)
    )
    messages_24h = await db.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.created_at >= since_24h,
            UsageEvent.action_type == "ai_reply",
        )
    )
    etg_spent_7d = await db.scalar(
        select(func.sum(UsageEvent.etg_charged)).where(
            UsageEvent.created_at >= since_7d
        )
    )
    conversations_24h = await db.scalar(
        select(func.count(Conversation.id)).where(
            Conversation.last_message_at >= since_24h
        )
    )

    return {
        "users": {"total": total_users},
        "businesses": {"total": total_businesses},
        "bots": {"total": total_bots, "active": active_bots},
        "agents": {"live": total_agents},
        "activity": {
            "messages_24h": messages_24h or 0,
            "conversations_24h": conversations_24h or 0,
            "etg_spent_7d": int(etg_spent_7d or 0),
        },
        "generated_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@router.get("/audit-log")
async def get_audit_log(
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
) -> list[dict]:
    stmt = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
    if action:
        stmt = stmt.where(AdminAuditLog.action.ilike(f"%{action}%"))
    if target_type:
        stmt = stmt.where(AdminAuditLog.target_type == target_type)
    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    logs = result.scalars().all()
    return [
        {
            "id": str(log.id),
            "admin_id": str(log.admin_id),
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "reason": log.reason,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

@router.post("/broadcast", status_code=202)
async def broadcast_notification(
    body: BroadcastRequest,
    current_admin: CurrentAdmin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_super_admin(current_admin)

    stmt = select(User).where(User.is_active.is_(True))
    if body.target_role:
        stmt = stmt.where(User.role == UserRole(body.target_role))

    result = await db.execute(stmt)
    users = result.scalars().all()

    for user in users:
        db.add(Notification(
            user_id=user.id,
            notification_type=body.notification_type,
            title=body.title,
            body=body.body,
            sent_via=[],
            is_read=False,
        ))

    await _audit(current_admin, "broadcast", "notification", "all",
                 new_value={"title": body.title, "recipient_count": len(users),
                            "target_role": body.target_role}, db=db)

    logger.admin_action(str(current_admin.id), "broadcast", "notification",
                        "all", {"recipients": len(users)})
    return {"queued_for": len(users)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_super_admin(user: User) -> None:
    if not (user.telegram_id and is_super_admin_telegram_id(user.telegram_id)):
        from app.core.exceptions import SuperAdminRequiredError
        raise SuperAdminRequiredError()


async def _audit(
    admin: User,
    action: str,
    target_type: str,
    target_id: str,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    reason: Optional[str] = None,
    db: AsyncSession = None,
) -> None:
    log = AdminAuditLog(
        admin_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        old_value=old_value,
        new_value=new_value,
        reason=reason,
    )
    if db:
        db.add(log)
