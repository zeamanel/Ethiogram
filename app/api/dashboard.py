# app/api/dashboard.py
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    Bot,
    BotStatus,
    Business,
    Conversation,
    KnowledgeDocument,
    Order,
    OrderStatus,
    TokenWallet,
    UsageEvent,
    ChatMessage,
)
from app.db.session import get_db, get_redis
from app.utils.text import slugify

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


class BusinessCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Business name cannot be blank")
        return v


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
            UsageEvent.business_id == str(business_id),
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
    )


@router.post("/businesses", response_model=BusinessSummary, status_code=201)
async def create_business(
    body: BusinessCreateRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BusinessSummary:
    """Create a business for the current user. Required before onboarding a bot."""
    slug = await _unique_slug(body.name, db)
    business = Business(
        owner_id=current_user.id,
        name=body.name,
        slug=slug,
        description=body.description,
        category=body.category,
        phone=body.phone,
        email=body.email,
    )
    db.add(business)
    await db.flush()

    logger.info(
        "Business created",
        business_id=str(business.id),
        owner_id=str(current_user.id),
        slug=slug,
    )
    return BusinessSummary(
        id=str(business.id),
        name=business.name,
        slug=business.slug,
        logo_url=business.logo_url,
        active_bots=0,
        total_bots=0,
        etg_balance=0,
        subscription_plan="free",
        is_suspended=business.is_suspended,
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

async def _unique_slug(name: str, db: AsyncSession) -> str:
    """Generate a URL-safe slug from name, appending -2, -3… on collision."""
    base = slugify(name)
    slug = base
    suffix = 1
    while await db.scalar(select(Business.id).where(Business.slug == slug)) is not None:
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


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
            UsageEvent.bot_id == str(bot_id),
            UsageEvent.created_at >= since_24h,
            UsageEvent.action_type == "ai_reply",
        )
    ) or 0

    messages_7d = await db.scalar(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.bot_id == str(bot_id),
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
            UsageEvent.bot_id == str(bot_id),
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
