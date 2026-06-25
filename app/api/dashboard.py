# app/api/dashboard.py
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    Agent,
    AgentTrial,
    AgentUnlock,
    Bot,
    BotStatus,
    Business,
    BusinessBrainConfig,
    ChildAgent,
    Conversation,
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeItem,
    Order,
    OrderStatus,
    TokenWallet,
    UsageEvent,
    ChatMessage,
)
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BusinessSummary(BaseModel):
    id: str
    name: str
    slug: str
    logo_url: Optional[str]
    active_bots: int
    total_bots: int
    etg_balance: int
    subscription_plan: str
    is_suspended: bool


class BotStats(BaseModel):
    bot_id: str
    bot_username: Optional[str]
    status: str
    messages_24h: int
    messages_7d: int
    unique_customers_7d: int
    etg_spent_7d: int
    avg_response_ms: Optional[float]


class UsageBreakdown(BaseModel):
    action_type: str
    events: int
    etg: int


class ActiveAgentSummary(BaseModel):
    id: str
    agent_id: str                # marketplace (father) agent id — lets the UI mark it deployed
    display_name: Optional[str]
    category: str
    is_active: bool
    status: str                  # "unlocked" | "trial"
    days_left: Optional[int] = None   # remaining trial days (trials only)


class BrainSummary(BaseModel):
    persona_name: Optional[str]
    total_chunks: int
    docs_by_status: dict          # {"embedded": N, "processing": N, "failed": N}
    total_items: int


class DashboardOverview(BaseModel):
    business: BusinessSummary
    bots: list[BotStats]
    usage_7d: list[UsageBreakdown]
    total_etg_7d: int
    total_messages_7d: int
    total_customers_7d: int
    total_orders: int
    pending_orders: int
    knowledge_documents: int
    conversations_today: int
    active_agents: list[ActiveAgentSummary]
    brain_summary: BrainSummary


class ConversationSummary(BaseModel):
    id: str
    customer_name: Optional[str]
    customer_username: Optional[str]
    detected_language: str
    total_messages: int
    total_etg_spent: int
    last_message_at: Optional[str]
    is_active: bool


class OrderSummary(BaseModel):
    id: str
    order_number: str
    customer_name: Optional[str]
    total: float
    currency: str
    order_status: str
    payment_status: str
    created_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/overview/{business_id}", response_model=DashboardOverview)
async def get_dashboard_overview(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> DashboardOverview:
    business = await _get_owned_business(business_id, current_user.id, db)

    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    # Wallet
    wallet_result = await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == business_id)
    )
    wallet = wallet_result.scalar_one_or_none()

    # Bots
    bots_result = await db.execute(
        select(Bot).where(Bot.business_id == business_id)
    )
    bots = bots_result.scalars().all()
    active_bots = sum(1 for b in bots if b.status == BotStatus.active)

    bot_stats = []
    for bot in bots:
        stats = await _get_bot_stats(bot.id, since_24h, since_7d, db)
        bot_stats.append(BotStats(
            bot_id=str(bot.id),
            bot_username=bot.bot_username,
            status=bot.status.value,
            **stats,
        ))

    # Usage 7d breakdown
    usage_result = await db.execute(
        select(
            UsageEvent.action_type,
            func.count(UsageEvent.id).label("events"),
            func.sum(UsageEvent.etg_charged).label("etg"),
        )
        .where(
            UsageEvent.business_id == business_id,
            UsageEvent.created_at >= since_7d,
        )
        .group_by(UsageEvent.action_type)
    )
    usage_rows = usage_result.fetchall()
    usage_breakdown = [
        UsageBreakdown(
            action_type=row.action_type,
            events=row.events,
            etg=int(row.etg or 0),
        )
        for row in usage_rows
    ]

    # Totals
    total_etg_7d = sum(u.etg for u in usage_breakdown)
    total_messages_7d = await db.scalar(
        select(func.count(ChatMessage.id))
        .join(ChatMessage.conversation)
        .where(
            Conversation.business_id == business_id,
            ChatMessage.created_at >= since_7d,
        )
    ) or 0

    total_customers_7d = await db.scalar(
        select(func.count(func.distinct(Conversation.customer_platform_id)))
        .where(
            Conversation.business_id == business_id,
            Conversation.last_message_at >= since_7d,
        )
    ) or 0

    # Orders
    total_orders = await db.scalar(
        select(func.count(Order.id)).where(Order.business_id == business_id)
    ) or 0
    pending_orders = await db.scalar(
        select(func.count(Order.id)).where(
            Order.business_id == business_id,
            Order.order_status == OrderStatus.pending,
        )
    ) or 0

    # Knowledge docs
    knowledge_docs = await db.scalar(
        select(func.count(KnowledgeDocument.id)).where(
            KnowledgeDocument.business_id == business_id
        )
    ) or 0

    # Conversations today
    conversations_today = await db.scalar(
        select(func.count(Conversation.id)).where(
            Conversation.business_id == business_id,
            Conversation.last_message_at >= since_24h,
        )
    ) or 0

    # Deployed agents + brain summary (gap-fill: keeps the console to one call)
    active_agents = await _load_active_agents(business_id, now, db)
    brain_summary = await _load_brain_summary(business_id, db)

    return DashboardOverview(
        business=BusinessSummary(
            id=str(business.id),
            name=business.name,
            slug=business.slug,
            logo_url=business.logo_url,
            active_bots=active_bots,
            total_bots=len(bots),
            etg_balance=wallet.balance if wallet else 0,
            subscription_plan=wallet.subscription_plan.value if wallet else "free",
            is_suspended=business.is_suspended,
        ),
        bots=bot_stats,
        usage_7d=usage_breakdown,
        total_etg_7d=total_etg_7d,
        total_messages_7d=total_messages_7d,
        total_customers_7d=total_customers_7d,
        total_orders=total_orders,
        pending_orders=pending_orders,
        knowledge_documents=knowledge_docs,
        conversations_today=conversations_today,
        active_agents=active_agents,
        brain_summary=brain_summary,
    )


async def _load_active_agents(
    business_id: uuid.UUID, now: datetime, db: AsyncSession
) -> list[ActiveAgentSummary]:
    """Deployed ChildAgents for the business, each tagged unlocked/trial."""
    rows = (await db.execute(
        select(ChildAgent, Agent)
        .join(Agent, ChildAgent.agent_id == Agent.id)
        .where(ChildAgent.business_id == business_id)
    )).all()

    out: list[ActiveAgentSummary] = []
    for child, father in rows:
        unlocked = await db.scalar(
            select(func.count(AgentUnlock.id)).where(
                AgentUnlock.child_agent_id == child.id,
                AgentUnlock.is_refunded.is_(False),
            )
        ) or 0
        status = "unlocked" if unlocked else "trial"
        days_left = None
        if status == "trial":
            trial = (await db.execute(
                select(AgentTrial)
                .where(AgentTrial.child_agent_id == child.id)
                .order_by(AgentTrial.created_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if trial and trial.expires_at:
                exp = trial.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                days_left = max(0, (exp - now).days)
        out.append(ActiveAgentSummary(
            id=str(child.id),
            agent_id=str(child.agent_id),
            display_name=child.display_name,
            category=father.category,
            is_active=child.is_active,
            status=status,
            days_left=days_left,
        ))
    return out


async def _load_brain_summary(business_id: uuid.UUID, db: AsyncSession) -> BrainSummary:
    """Persona + knowledge readiness for the business brain."""
    brain = (await db.execute(
        select(BusinessBrainConfig).where(BusinessBrainConfig.business_id == business_id)
    )).scalar_one_or_none()

    total_chunks = await db.scalar(
        select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.business_id == business_id)
    ) or 0

    status_rows = (await db.execute(
        select(KnowledgeDocument.status, func.count(KnowledgeDocument.id))
        .where(KnowledgeDocument.business_id == business_id)
        .group_by(KnowledgeDocument.status)
    )).all()
    counts: dict[str, int] = {}
    for status_val, n in status_rows:
        key = status_val.value if hasattr(status_val, "value") else str(status_val)
        counts[key] = n
    docs_by_status = {
        "embedded": counts.get("completed", 0),   # completed == embedded & searchable
        "processing": counts.get("processing", 0),
        "failed": counts.get("failed", 0),
    }

    total_items = await db.scalar(
        select(func.count(KnowledgeItem.id)).where(KnowledgeItem.business_id == business_id)
    ) or 0

    return BrainSummary(
        persona_name=brain.persona_name if brain else None,
        total_chunks=total_chunks,
        docs_by_status=docs_by_status,
        total_items=total_items,
    )


@router.get("/businesses", response_model=list[BusinessSummary])
async def list_businesses(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[BusinessSummary]:
    result = await db.execute(
        select(Business)
        .where(Business.owner_id == current_user.id)
        .order_by(Business.created_at.desc())
    )
    businesses = result.scalars().all()

    summaries = []
    for biz in businesses:
        bots_result = await db.execute(
            select(func.count(Bot.id), func.count(
                Bot.id if Bot.status == BotStatus.active else None
            )).where(Bot.business_id == biz.id)
        )
        wallet_result = await db.execute(
            select(TokenWallet).where(TokenWallet.business_id == biz.id)
        )
        wallet = wallet_result.scalar_one_or_none()

        bots_total = await db.scalar(
            select(func.count(Bot.id)).where(Bot.business_id == biz.id)
        ) or 0
        bots_active = await db.scalar(
            select(func.count(Bot.id)).where(
                Bot.business_id == biz.id, Bot.status == BotStatus.active
            )
        ) or 0

        summaries.append(BusinessSummary(
            id=str(biz.id),
            name=biz.name,
            slug=biz.slug,
            logo_url=biz.logo_url,
            active_bots=bots_active,
            total_bots=bots_total,
            etg_balance=wallet.balance if wallet else 0,
            subscription_plan=wallet.subscription_plan.value if wallet else "free",
            is_suspended=biz.is_suspended,
        ))
    return summaries


@router.get("/conversations/{business_id}", response_model=list[ConversationSummary])
async def list_conversations(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    active_only: bool = Query(False),
) -> list[ConversationSummary]:
    await _get_owned_business(business_id, current_user.id, db)

    stmt = (
        select(Conversation)
        .where(Conversation.business_id == business_id)
        .order_by(Conversation.last_message_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if active_only:
        stmt = stmt.where(Conversation.is_active.is_(True))

    result = await db.execute(stmt)
    convs = result.scalars().all()
    return [
        ConversationSummary(
            id=str(c.id),
            customer_name=c.customer_name,
            customer_username=c.customer_username,
            detected_language=c.detected_language,
            total_messages=c.total_messages,
            total_etg_spent=c.total_etg_spent,
            last_message_at=c.last_message_at.isoformat() if c.last_message_at else None,
            is_active=c.is_active,
        )
        for c in convs
    ]


@router.get("/orders/{business_id}", response_model=list[OrderSummary])
async def list_orders(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
) -> list[OrderSummary]:
    await _get_owned_business(business_id, current_user.id, db)

    stmt = (
        select(Order)
        .where(Order.business_id == business_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(Order.order_status == OrderStatus(status))

    result = await db.execute(stmt)
    orders = result.scalars().all()
    return [
        OrderSummary(
            id=str(o.id),
            order_number=o.order_number,
            customer_name=o.customer_name,
            total=o.total,
            currency=o.currency,
            order_status=o.order_status.value,
            payment_status=o.payment_status.value,
            created_at=o.created_at.isoformat(),
        )
        for o in orders
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_owned_business(
    business_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession
) -> Business:
    result = await db.execute(
        select(Business).where(
            Business.id == business_id,
            Business.owner_id == user_id,
        )
    )
    biz = result.scalar_one_or_none()
    if biz is None:
        raise NotFoundError("Business", str(business_id))
    return biz


async def _get_bot_stats(
    bot_id: uuid.UUID,
    since_24h: datetime,
    since_7d: datetime,
    db: AsyncSession,
) -> dict:
    messages_24h = await db.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.bot_id == bot_id,
            UsageEvent.created_at >= since_24h,
            UsageEvent.action_type == "ai_reply",
        )
    ) or 0

    messages_7d = await db.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.bot_id == bot_id,
            UsageEvent.created_at >= since_7d,
            UsageEvent.action_type == "ai_reply",
        )
    ) or 0

    unique_customers_7d = await db.scalar(
        select(func.count(func.distinct(Conversation.customer_platform_id))).where(
            Conversation.bot_id == bot_id,
            Conversation.last_message_at >= since_7d,
        )
    ) or 0

    etg_spent_7d = await db.scalar(
        select(func.sum(UsageEvent.etg_charged)).where(
            UsageEvent.bot_id == bot_id,
            UsageEvent.created_at >= since_7d,
        )
    ) or 0

    avg_ms_result = await db.scalar(
        select(func.avg(ChatMessage.latency_ms)).where(
            ChatMessage.latency_ms.isnot(None),
            ChatMessage.created_at >= since_7d,
        )
    )

    return {
        "messages_24h": messages_24h,
        "messages_7d": messages_7d,
        "unique_customers_7d": unique_customers_7d,
        "etg_spent_7d": int(etg_spent_7d),
        "avg_response_ms": float(avg_ms_result) if avg_ms_result else None,
    }
