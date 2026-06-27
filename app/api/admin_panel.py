# app/api/admin_panel.py
"""Admin dashboard API — global platform management for the Mini App.

Distinct from app/api/admin.py (server-to-server ops gated by a secret header):
these endpoints are gated by CurrentAdminUser (a logged-in Telegram user whose
is_admin flag is set), so the admin can drive them straight from the Mini App.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdminUser
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import (
    AdminAuditLog,
    Agent,
    AiModel,
    Bot,
    Business,
    ChildAgent,
    TokenWallet,
    UsageEvent,
    User,
    UserRole,
)
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin-panel"])


async def _audit(db, admin: User, action: str, target_type: str, target_id: str,
                 old=None, new=None, request: Optional[Request] = None) -> None:
    db.add(AdminAuditLog(
        admin_id=admin.id, action=action, target_type=target_type, target_id=str(target_id),
        old_value=old, new_value=new,
        ip_address=(request.client.host if request and request.client else None),
        user_agent=(request.headers.get("user-agent") if request else None),
    ))


# ── stats ────────────────────────────────────────────────────────────────────

class AdminStats(BaseModel):
    total_businesses: int
    active_businesses: int
    suspended_businesses: int
    deleted_businesses: int
    total_users: int
    total_bots: int
    total_agents_deployed: int
    total_etg_spent: int
    total_revenue_etg: int


@router.get("/stats", response_model=AdminStats)
async def get_stats(current_admin: CurrentAdminUser, db: AsyncSession = Depends(get_db)) -> AdminStats:
    total_biz = await db.scalar(select(func.count(Business.id))) or 0
    deleted_biz = await db.scalar(
        select(func.count(Business.id)).where(Business.deleted_at.is_not(None))) or 0
    suspended_biz = await db.scalar(
        select(func.count(Business.id)).where(
            Business.deleted_at.is_(None), Business.is_suspended.is_(True))) or 0
    active_biz = total_biz - deleted_biz - suspended_biz
    return AdminStats(
        total_businesses=total_biz,
        active_businesses=active_biz,
        suspended_businesses=suspended_biz,
        deleted_businesses=deleted_biz,
        total_users=await db.scalar(
            select(func.count(User.id)).where(
                User.is_admin.is_(False), User.deleted_at.is_(None))) or 0,
        total_bots=await db.scalar(select(func.count(Bot.id))) or 0,
        total_agents_deployed=await db.scalar(select(func.count(ChildAgent.id))) or 0,
        total_etg_spent=await db.scalar(select(func.coalesce(func.sum(UsageEvent.etg_charged), 0))) or 0,
        total_revenue_etg=await db.scalar(select(func.coalesce(func.sum(TokenWallet.lifetime_recharged), 0))) or 0,
    )


# ── businesses ────────────────────────────────────────────────────────────────

class AdminBusinessRow(BaseModel):
    id: str
    name: str
    slug: str
    owner_email: Optional[str]
    created_at: str
    bot_count: int
    agent_count: int
    is_suspended: bool
    is_deleted: bool


class AdminBusinessList(BaseModel):
    items: list[AdminBusinessRow]
    total: int
    limit: int
    offset: int


@router.get("/businesses", response_model=AdminBusinessList)
async def list_businesses(
    current_admin: CurrentAdminUser,
    status: Optional[str] = Query(None, description="active | suspended | deleted"),
    search: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AdminBusinessList:
    bot_count = (select(func.count(Bot.id)).where(Bot.business_id == Business.id)
                 .correlate(Business).scalar_subquery())
    agent_count = (select(func.count(ChildAgent.id)).where(ChildAgent.business_id == Business.id)
                   .correlate(Business).scalar_subquery())

    conds = []
    if status == "active":
        conds += [Business.deleted_at.is_(None), Business.is_suspended.is_(False)]
    elif status == "suspended":
        conds += [Business.deleted_at.is_(None), Business.is_suspended.is_(True)]
    elif status == "deleted":
        conds += [Business.deleted_at.is_not(None)]
    if search:
        like = f"%{search}%"
        conds.append(Business.name.ilike(like) | User.email.ilike(like))

    base = select(Business, User.email, bot_count.label("bc"), agent_count.label("ac")).outerjoin(
        User, Business.owner_id == User.id)
    for c in conds:
        base = base.where(c)

    total = await db.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())) or 0
    rows = (await db.execute(
        base.order_by(Business.created_at.desc()).limit(limit).offset(offset))).all()

    items = [AdminBusinessRow(
        id=str(b.id), name=b.name, slug=b.slug, owner_email=email,
        created_at=b.created_at.isoformat() if b.created_at else "",
        bot_count=bc or 0, agent_count=ac or 0,
        is_suspended=b.is_suspended, is_deleted=b.deleted_at is not None,
    ) for (b, email, bc, ac) in rows]
    return AdminBusinessList(items=items, total=total, limit=limit, offset=offset)


class SuspendRequest(BaseModel):
    suspend: bool
    reason: Optional[str] = None


@router.patch("/businesses/{business_id}/suspend", response_model=AdminBusinessRow)
async def suspend_business(
    business_id: uuid.UUID,
    body: SuspendRequest,
    current_admin: CurrentAdminUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> AdminBusinessRow:
    biz = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
    if biz is None:
        raise NotFoundError("Business", str(business_id))
    was = biz.is_suspended
    biz.is_suspended = body.suspend
    biz.suspended_reason = body.reason if body.suspend else None
    await _audit(db, current_admin, "business.suspend" if body.suspend else "business.unsuspend",
                 "business", business_id, {"is_suspended": was}, {"is_suspended": body.suspend}, request)
    # refresh public caches so a suspended store/site goes dark immediately
    from app.api.miniapp import bust_storefront_cache
    await bust_storefront_cache(business_id, db, redis)
    bot_count = await db.scalar(select(func.count(Bot.id)).where(Bot.business_id == business_id)) or 0
    agent_count = await db.scalar(select(func.count(ChildAgent.id)).where(ChildAgent.business_id == business_id)) or 0
    email = await db.scalar(select(User.email).where(User.id == biz.owner_id))
    logger.info("Business suspension toggled", business_id=str(business_id), suspend=body.suspend)
    return AdminBusinessRow(
        id=str(biz.id), name=biz.name, slug=biz.slug, owner_email=email,
        created_at=biz.created_at.isoformat() if biz.created_at else "",
        bot_count=bot_count, agent_count=agent_count,
        is_suspended=biz.is_suspended, is_deleted=biz.deleted_at is not None,
    )


@router.delete("/businesses/{business_id}", status_code=204)
async def delete_business(
    business_id: uuid.UUID,
    current_admin: CurrentAdminUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> None:
    """Hard-delete a business and all its data (FK cascades). Logged."""
    biz = (await db.execute(select(Business).where(Business.id == business_id))).scalar_one_or_none()
    if biz is None:
        raise NotFoundError("Business", str(business_id))
    from app.api.miniapp import bust_storefront_cache
    await bust_storefront_cache(business_id, db, redis)
    await _audit(db, current_admin, "business.delete", "business", business_id,
                 {"name": biz.name, "slug": biz.slug}, None, request)
    await db.delete(biz)   # cascades to bots, agents, knowledge, configs, domains…
    logger.warning("Business hard-deleted", business_id=str(business_id), admin_id=str(current_admin.id))


# ── users ────────────────────────────────────────────────────────────────────

class AdminUserRow(BaseModel):
    id: str
    email: Optional[str]
    telegram_id: Optional[int]
    full_name: Optional[str]
    is_admin: bool
    created_at: str


class AdminUserList(BaseModel):
    items: list[AdminUserRow]
    total: int
    limit: int
    offset: int


@router.get("/users", response_model=AdminUserList)
async def list_users(
    current_admin: CurrentAdminUser,
    search: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AdminUserList:
    stmt = select(User).where(User.deleted_at.is_(None))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(User.email.ilike(like) | User.full_name.ilike(like) | User.username.ilike(like))
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(
        stmt.order_by(User.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    items = [AdminUserRow(
        id=str(u.id), email=u.email, telegram_id=u.telegram_id, full_name=u.full_name,
        is_admin=bool(u.is_admin or u.role == UserRole.admin),
        created_at=u.created_at.isoformat() if u.created_at else "",
    ) for u in rows]
    return AdminUserList(items=items, total=total, limit=limit, offset=offset)


class AdminToggleRequest(BaseModel):
    is_admin: bool


@router.patch("/users/{user_id}/admin", response_model=AdminUserRow)
async def set_user_admin(
    user_id: uuid.UUID,
    body: AdminToggleRequest,
    current_admin: CurrentAdminUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminUserRow:
    if user_id == current_admin.id and not body.is_admin:
        raise ValidationError("You can't revoke your own admin access")
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise NotFoundError("User", str(user_id))
    was = bool(u.is_admin)
    u.is_admin = body.is_admin
    # keep role consistent so legacy role-based checks agree
    if body.is_admin and u.role != UserRole.admin:
        u.role = UserRole.admin
    elif not body.is_admin and u.role == UserRole.admin:
        u.role = UserRole.owner
    await _audit(db, current_admin, "user.set_admin", "user", user_id,
                 {"is_admin": was}, {"is_admin": body.is_admin}, request)
    logger.info("Admin rights changed", user_id=str(user_id), is_admin=body.is_admin)
    return AdminUserRow(
        id=str(u.id), email=u.email, telegram_id=u.telegram_id, full_name=u.full_name,
        is_admin=bool(u.is_admin or u.role == UserRole.admin),
        created_at=u.created_at.isoformat() if u.created_at else "",
    )


# ── system status ─────────────────────────────────────────────────────────────

class SystemStatus(BaseModel):
    database: dict
    redis: dict
    storage: dict
    workers: dict


@router.get("/system/status", response_model=SystemStatus)
async def system_status(
    current_admin: CurrentAdminUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> SystemStatus:
    # DB
    try:
        await db.execute(select(1))
        database = {"status": "ok"}
    except Exception as exc:  # pragma: no cover
        database = {"status": "error", "detail": str(exc)[:200]}
    # Redis
    try:
        pong = await redis.ping()
        redis_st = {"status": "ok" if pong else "degraded"}
    except Exception as exc:
        redis_st = {"status": "error", "detail": str(exc)[:200]}
    # Storage (best-effort; never fail the whole check)
    try:
        from app.services.storage_service import storage_service
        from app.core.config import settings
        storage = {"status": "configured", "bucket": settings.gcs_bucket_public}
        _ = storage_service  # presence only; a live HEAD would add latency/cost
    except Exception as exc:  # pragma: no cover
        storage = {"status": "error", "detail": str(exc)[:200]}
    # Workers — surface a health signal we can compute cheaply: embedding backlog.
    from app.db.models import KnowledgeDocument, DocumentStatus
    pending = await db.scalar(
        select(func.count(KnowledgeDocument.id)).where(
            KnowledgeDocument.status == DocumentStatus.pending)) or 0
    workers = {
        "jobs": ["embedding", "notification", "trial-monitor", "escrow-release"],
        "embedding_backlog": pending,
        "status": "ok" if pending < 50 else "backlogged",
    }
    return SystemStatus(database=database, redis=redis_st, storage=storage, workers=workers)


# ── agent model management (admin sets the model for a father agent) ──────────

class ModelOption(BaseModel):
    model_id: str
    display_name: str
    tier: str


@router.get("/models", response_model=list[ModelOption])
async def list_models(current_admin: CurrentAdminUser, db: AsyncSession = Depends(get_db)) -> list[ModelOption]:
    """The model catalog admins can assign to father agents (enabled models)."""
    rows = (await db.execute(
        select(AiModel).where(AiModel.is_enabled.is_(True)).order_by(AiModel.display_name)
    )).scalars().all()
    return [ModelOption(
        model_id=m.model_id, display_name=m.display_name,
        tier=m.tier.value if hasattr(m.tier, "value") else str(m.tier),
    ) for m in rows]


class AdminAgentRow(BaseModel):
    id: str
    name: str
    category: str
    status: str
    preferred_model_id: Optional[str]
    deployments: int
    total_unlocks: int


@router.get("/agents", response_model=list[AdminAgentRow])
async def list_agents(
    current_admin: CurrentAdminUser,
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[AdminAgentRow]:
    """All father (marketplace) agents with their current model + deployment count."""
    deployments = (select(func.count(ChildAgent.id))
                   .where(ChildAgent.agent_id == Agent.id).correlate(Agent).scalar_subquery())
    stmt = select(Agent, deployments.label("dc"))
    if search:
        stmt = stmt.where(Agent.name.ilike(f"%{search}%"))
    rows = (await db.execute(stmt.order_by(Agent.name))).all()
    return [AdminAgentRow(
        id=str(a.id), name=a.name, category=a.category,
        status=a.status.value if hasattr(a.status, "value") else str(a.status),
        preferred_model_id=a.preferred_model_id, deployments=dc or 0,
        total_unlocks=a.total_unlocks or 0,
    ) for (a, dc) in rows]


class SetAgentModelRequest(BaseModel):
    model_id: Optional[str] = None      # None clears it (agent falls back to platform default)


@router.patch("/agents/{agent_id}/model", response_model=AdminAgentRow)
async def set_agent_model(
    agent_id: uuid.UUID,
    body: SetAgentModelRequest,
    current_admin: CurrentAdminUser,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminAgentRow:
    """Set (or clear) the model a father agent runs on. Takes effect on every
    deploying business's next message — the model is read fresh per reply."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise NotFoundError("Agent", str(agent_id))
    if body.model_id is not None:
        ok = await db.scalar(select(AiModel.id).where(
            AiModel.model_id == body.model_id, AiModel.is_enabled.is_(True)))
        if ok is None:
            raise ValidationError(f"Unknown or disabled model: {body.model_id}")
    old = agent.preferred_model_id
    agent.preferred_model_id = body.model_id
    await _audit(db, current_admin, "agent.set_model", "agent", agent_id,
                 {"model_id": old}, {"model_id": body.model_id}, request)
    logger.info("Agent model changed", agent_id=str(agent_id),
                old=old, new=body.model_id, admin_id=str(current_admin.id))
    deployments = await db.scalar(
        select(func.count(ChildAgent.id)).where(ChildAgent.agent_id == agent_id)) or 0
    return AdminAgentRow(
        id=str(agent.id), name=agent.name, category=agent.category,
        status=agent.status.value if hasattr(agent.status, "value") else str(agent.status),
        preferred_model_id=agent.preferred_model_id, deployments=deployments,
        total_unlocks=agent.total_unlocks or 0,
    )
