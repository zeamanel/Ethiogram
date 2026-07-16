# app/api/agents.py
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, CurrentUser
from app.core.config import settings
from app.core.exceptions import (
    AgentNotLiveError,
    AgentTrialExpiredError,
    AlreadyExistsError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import decrypt_agent_prompt, encrypt_agent_prompt, encrypt_child_secrets
from app.db.models import (
    Agent,
    AgentReview,
    AgentStatus,
    AgentTrial,
    AgentUnlock,
    Business,
    ChildAgent,
    CreatorProfile,
    TokenEscrow,
    TokenWallet,
    EscrowStatus,
)
from app.db.session import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AgentPublicResponse(BaseModel):
    id: str
    name: str
    tagline: str
    description: str
    category: str
    tags: list
    capabilities: list
    child_schema: Optional[dict]
    setup_guide: Optional[str]
    price_etg: int
    preferred_model_id: Optional[str]
    status: str
    is_featured: bool
    is_staff_pick: bool
    cover_image_url: Optional[str]
    total_unlocks: int
    average_rating: Optional[float]
    review_count: int
    created_at: str


class PublishAgentRequest(BaseModel):
    name: str
    tagline: str
    description: str
    category: str
    tags: list[str] = []
    capabilities: list[str] = []
    system_prompt: str
    child_schema: Optional[dict] = None
    setup_guide: Optional[str] = None
    price_etg: int = 0
    preferred_model_id: Optional[str] = None
    cover_image_url: Optional[str] = None

    @field_validator("system_prompt")
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("system_prompt cannot be empty")
        return v

    @field_validator("price_etg")
    @classmethod
    def price_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("price_etg must be >= 0")
        return v


class StartTrialRequest(BaseModel):
    business_id: uuid.UUID
    child_data: Optional[dict] = None
    child_secrets: Optional[dict] = None   # credentials/API keys — encrypted at rest


class UnlockRequest(BaseModel):
    business_id: uuid.UUID
    child_data: Optional[dict] = None
    child_secrets: Optional[dict] = None   # credentials/API keys — encrypted at rest


class ChildAgentUpdateRequest(BaseModel):
    # All fields optional → partial updates. A pause/resume or rename does not
    # require resending the whole child_data config.
    child_data: Optional[dict] = None
    child_secrets: Optional[dict] = None   # credentials/API keys — encrypted at rest
    display_name: Optional[str] = None
    is_active: Optional[bool] = None
    # Bind this agent to ONE of the business's bots ("" = run on all bots).
    # None (omitted) = unchanged — standard partial-PATCH semantics.
    assigned_bot_id: Optional[str] = None


class ChildAgentDetailResponse(BaseModel):
    """A deployed agent's editable config. NEVER includes secret values —
    only a boolean saying whether credentials are configured."""
    id: str
    agent_id: str
    agent_name: str
    category: str
    display_name: Optional[str]
    is_active: bool
    status: str                      # "trial" | "unlocked"
    days_left: Optional[int] = None  # remaining trial days (trials only)
    child_data: dict
    child_schema: Optional[dict] = None   # author's field contract (for the UI)
    setup_guide: Optional[str] = None
    has_secrets: bool
    assigned_bot_id: Optional[str] = None   # None = runs on all the business's bots


class ReviewRequest(BaseModel):
    rating: int
    review_text: Optional[str] = None

    @field_validator("rating")
    @classmethod
    def rating_range(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("rating must be between 1 and 5")
        return v


# ---------------------------------------------------------------------------
# Marketplace browsing (public)
# ---------------------------------------------------------------------------

@router.get("", response_model=list[AgentPublicResponse])
async def list_agents(
    db: AsyncSession = Depends(get_db),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    featured_only: bool = Query(False),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
) -> list[AgentPublicResponse]:
    stmt = select(Agent).where(Agent.status == AgentStatus.live)
    if category:
        stmt = stmt.where(Agent.category == category)
    if featured_only:
        stmt = stmt.where(Agent.is_featured.is_(True))
    if search:
        stmt = stmt.where(Agent.name.ilike(f"%{search}%"))
    stmt = stmt.order_by(Agent.is_featured.desc(), Agent.total_unlocks.desc()).limit(limit).offset(offset)

    result = await db.execute(stmt)
    agents = result.scalars().all()
    return [_agent_to_response(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentPublicResponse)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentPublicResponse:
    agent = await _get_live_agent(agent_id, db)
    return _agent_to_response(agent)


# ---------------------------------------------------------------------------
# Creator — publish / manage
# ---------------------------------------------------------------------------

@router.post("", response_model=AgentPublicResponse, status_code=201)
async def publish_agent(
    body: PublishAgentRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> AgentPublicResponse:
    profile_result = await db.execute(
        select(CreatorProfile).where(CreatorProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise PermissionDeniedError("Creator profile required to publish agents")

    encrypted_prompt, key_ref = encrypt_agent_prompt(
        body.system_prompt, str(uuid.uuid4())
    )

    # Trusted creators are auto-approved; new creators go to pending_review
    initial_status = (
        AgentStatus.live if profile.is_trusted else AgentStatus.pending_review
    )

    agent = Agent(
        creator_id=profile.id,
        name=body.name,
        tagline=body.tagline,
        description=body.description,
        category=body.category,
        tags=body.tags,
        capabilities=body.capabilities,
        encrypted_system_prompt=encrypted_prompt,
        encryption_key_ref=key_ref,
        child_schema=body.child_schema,
        setup_guide=body.setup_guide,
        price_etg=body.price_etg,
        preferred_model_id=body.preferred_model_id,
        cover_image_url=body.cover_image_url,
        status=initial_status,
    )
    db.add(agent)
    await db.flush()

    logger.info(
        "Agent published",
        agent_id=str(agent.id),
        creator_id=str(profile.id),
        status=initial_status.value,
    )
    return _agent_to_response(agent)


@router.get("/mine", response_model=list[AgentPublicResponse])
async def list_my_agents(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[AgentPublicResponse]:
    profile_result = await db.execute(
        select(CreatorProfile.id).where(CreatorProfile.user_id == current_user.id)
    )
    profile_id = profile_result.scalar_one_or_none()
    if profile_id is None:
        return []

    result = await db.execute(
        select(Agent)
        .where(Agent.creator_id == profile_id)
        .order_by(Agent.created_at.desc())
    )
    return [_agent_to_response(a) for a in result.scalars().all()]


# ---------------------------------------------------------------------------
# Trial
# ---------------------------------------------------------------------------

@router.post("/{agent_id}/trial", status_code=201)
async def start_trial(
    agent_id: uuid.UUID,
    body: StartTrialRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    agent = await _get_live_agent(agent_id, db)
    await _assert_owns_business(current_user.id, body.business_id, db)

    # One trial per agent per business
    existing = await db.execute(
        select(AgentTrial).where(
            AgentTrial.agent_id == agent_id,
            AgentTrial.business_id == body.business_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise AlreadyExistsError("AgentTrial")

    # Reuse an existing ChildAgent for this (agent, business) if one is already
    # deployed (e.g. a prior unlock or a retried trial) — inserting a duplicate
    # would violate uq_child_agent_business and surface as a 500.
    child = (await db.execute(
        select(ChildAgent).where(
            ChildAgent.agent_id == agent_id,
            ChildAgent.business_id == body.business_id,
        )
    )).scalar_one_or_none()
    if child is None:
        # Enforce the per-business deployment cap (was dead config until now).
        deployed = await db.scalar(select(func.count(ChildAgent.id)).where(
            ChildAgent.business_id == body.business_id))
        if (deployed or 0) >= settings.max_child_agents_per_bot:
            raise ValidationError(
                f"Agent limit reached ({settings.max_child_agents_per_bot}). "
                "Remove an agent before deploying another.")
        child = ChildAgent(
            agent_id=agent_id,
            business_id=body.business_id,
            child_data=body.child_data or {},
            child_secrets=encrypt_child_secrets(body.child_secrets) if body.child_secrets else None,
        )
        db.add(child)
        await db.flush()
    else:
        if body.child_data:
            child.child_data = body.child_data
        if body.child_secrets:
            child.child_secrets = encrypt_child_secrets(body.child_secrets)

    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.trial_duration_days)
    trial = AgentTrial(
        agent_id=agent_id,
        business_id=body.business_id,
        child_agent_id=child.id,
        expires_at=expires_at,
    )
    db.add(trial)

    logger.info("Trial started", agent_id=str(agent_id), business_id=str(body.business_id))
    return {
        "child_agent_id": str(child.id),
        "trial_expires_at": expires_at.isoformat(),
        "days_remaining": settings.trial_duration_days,
    }


# ---------------------------------------------------------------------------
# Unlock (purchase)
# ---------------------------------------------------------------------------

@router.post("/{agent_id}/unlock", status_code=201)
async def unlock_agent(
    agent_id: uuid.UUID,
    body: UnlockRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    agent = await _get_live_agent(agent_id, db)
    await _assert_owns_business(current_user.id, body.business_id, db)

    # Check wallet balance
    wallet_result = await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == body.business_id)
    )
    wallet = wallet_result.scalar_one_or_none()
    if wallet is None or wallet.balance < agent.price_etg:
        from app.core.exceptions import InsufficientBalanceError
        raise InsufficientBalanceError(
            required=agent.price_etg,
            available=wallet.balance if wallet else 0,
        )

    # Deduct ETG and place in escrow
    balance_before = wallet.balance
    wallet.balance -= agent.price_etg
    wallet.escrow_balance += agent.price_etg

    escrow = TokenEscrow(
        wallet_id=wallet.id,
        amount=agent.price_etg,
        reason=f"Agent unlock: {agent.name}",
        release_at=datetime.now(timezone.utc) + timedelta(days=settings.escrow_release_days),
        status=EscrowStatus.holding,
    )
    db.add(escrow)
    await db.flush()

    # Get or create ChildAgent
    child_result = await db.execute(
        select(ChildAgent).where(
            ChildAgent.agent_id == agent_id,
            ChildAgent.business_id == body.business_id,
        )
    )
    child = child_result.scalar_one_or_none()
    if child is None:
        child = ChildAgent(
            agent_id=agent_id,
            business_id=body.business_id,
            child_data=body.child_data or {},
            child_secrets=encrypt_child_secrets(body.child_secrets) if body.child_secrets else None,
        )
        db.add(child)
        await db.flush()
    else:
        if body.child_data:
            child.child_data = body.child_data
        if body.child_secrets:
            child.child_secrets = encrypt_child_secrets(body.child_secrets)

    unlock = AgentUnlock(
        agent_id=agent_id,
        business_id=body.business_id,
        child_agent_id=child.id,
        etg_paid=agent.price_etg,
        escrow_id=escrow.id,
    )
    db.add(unlock)

    # Mark trial as converted if one exists
    trial_result = await db.execute(
        select(AgentTrial).where(
            AgentTrial.agent_id == agent_id,
            AgentTrial.business_id == body.business_id,
        )
    )
    trial = trial_result.scalar_one_or_none()
    if trial:
        trial.is_converted = True
        trial.converted_at = datetime.now(timezone.utc)

    logger.info(
        "Agent unlocked",
        agent_id=str(agent_id),
        business_id=str(body.business_id),
        etg_paid=agent.price_etg,
    )
    return {
        "child_agent_id": str(child.id),
        "etg_paid": agent.price_etg,
        "escrow_release_at": escrow.release_at.isoformat(),
        "dispute_window_days": settings.escrow_release_days,
    }


# ---------------------------------------------------------------------------
# Child Agent management
# ---------------------------------------------------------------------------

@router.get("/child/{child_agent_id}", response_model=ChildAgentDetailResponse)
async def get_child_agent(
    child_agent_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ChildAgentDetailResponse:
    """Fetch a deployed agent's editable config to populate the manage UI.
    Secret credentials are never returned — only has_secrets."""
    child = await _get_owned_child_agent(child_agent_id, current_user.id, db)
    father = await db.get(Agent, child.agent_id)
    status, days_left = await _child_status(child, db)
    return ChildAgentDetailResponse(
        id=str(child.id),
        agent_id=str(child.agent_id),
        agent_name=father.name if father else "Agent",
        category=father.category if father else "",
        display_name=child.display_name,
        is_active=child.is_active,
        status=status,
        days_left=days_left,
        child_data=child.child_data or {},
        child_schema=father.child_schema if father else None,
        setup_guide=father.setup_guide if father else None,
        has_secrets=child.child_secrets is not None,
        assigned_bot_id=str(child.assigned_to_bot_id) if child.assigned_to_bot_id else None,
    )


@router.patch("/child/{child_agent_id}", status_code=204)
async def update_child_agent(
    child_agent_id: uuid.UUID,
    body: ChildAgentUpdateRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    child = await _get_owned_child_agent(child_agent_id, current_user.id, db)
    if body.child_data is not None:
        child.child_data = body.child_data
    if body.child_secrets is not None:
        child.child_secrets = encrypt_child_secrets(body.child_secrets)
    if body.display_name is not None:
        child.display_name = body.display_name
    if body.is_active is not None:
        child.is_active = body.is_active
    if body.assigned_bot_id is not None:
        if body.assigned_bot_id == "":
            child.assigned_to_bot_id = None          # back to "all bots"
        else:
            from app.db.models import Bot
            try:
                bot_uuid = uuid.UUID(body.assigned_bot_id)
            except ValueError:
                raise ValidationError("assigned_bot_id must be a bot UUID or empty")
            bot_ok = await db.scalar(select(Bot.id).where(
                Bot.id == bot_uuid, Bot.business_id == child.business_id))
            if bot_ok is None:
                raise NotFoundError("Bot", body.assigned_bot_id)
            child.assigned_to_bot_id = bot_uuid


@router.delete("/child/{child_agent_id}/secrets", status_code=204)
async def disconnect_child_secrets(
    child_agent_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Clear a deployed agent's credentials. A dedicated endpoint because the
    partial PATCH treats child_secrets=None as 'unchanged', so it can't express
    'remove'. Setting the column NULL makes has_secrets False again. Idempotent."""
    child = await _get_owned_child_agent(child_agent_id, current_user.id, db)
    child.child_secrets = None


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

@router.post("/{agent_id}/reviews", status_code=201)
async def submit_review(
    agent_id: uuid.UUID,
    body: ReviewRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Verify verified purchase
    unlock_result = await db.execute(
        select(AgentUnlock)
        .join(AgentUnlock.agent)
        .where(
            AgentUnlock.agent_id == agent_id,
            Agent.creator_id.in_(
                select(CreatorProfile.id).where(CreatorProfile.user_id != current_user.id)
            ),
        )
    )
    unlock = unlock_result.scalar_one_or_none()
    if unlock is None:
        raise PermissionDeniedError("Must have unlocked this agent to leave a review")

    # One review per business
    existing = await db.execute(
        select(AgentReview).where(
            AgentReview.agent_id == agent_id,
            AgentReview.business_id == unlock.business_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise AlreadyExistsError("AgentReview")

    review = AgentReview(
        agent_id=agent_id,
        business_id=unlock.business_id,
        unlock_id=unlock.id,
        rating=body.rating,
        review_text=body.review_text,
    )
    db.add(review)
    await db.flush()

    # Update agent aggregate rating
    await _update_agent_rating(agent_id, db)

    return {"id": str(review.id)}


@router.get("/{agent_id}/reviews", response_model=list[dict])
async def list_reviews(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    result = await db.execute(
        select(AgentReview)
        .where(
            AgentReview.agent_id == agent_id,
            AgentReview.is_visible.is_(True),
        )
        .order_by(AgentReview.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    reviews = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "rating": r.rating,
            "review_text": r.review_text,
            "created_at": r.created_at.isoformat(),
        }
        for r in reviews
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_live_agent(agent_id: uuid.UUID, db: AsyncSession) -> Agent:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise NotFoundError("Agent", str(agent_id))
    if agent.status != AgentStatus.live:
        raise AgentNotLiveError(agent.status.value)
    return agent


async def _assert_owns_business(
    user_id: uuid.UUID, business_id: uuid.UUID, db: AsyncSession
) -> None:
    result = await db.execute(
        select(Business.id).where(
            Business.id == business_id,
            Business.owner_id == user_id,
            Business.is_suspended.is_(False),
            Business.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Business", str(business_id))


async def _child_status(child: ChildAgent, db: AsyncSession) -> tuple[str, Optional[int]]:
    """Classify a deployed agent as unlocked/trial and compute remaining trial days."""
    unlocked = await db.scalar(
        select(func.count(AgentUnlock.id)).where(
            AgentUnlock.child_agent_id == child.id,
            AgentUnlock.is_refunded.is_(False),
        )
    ) or 0
    if unlocked:
        return "unlocked", None
    trial = (await db.execute(
        select(AgentTrial)
        .where(AgentTrial.child_agent_id == child.id)
        .order_by(AgentTrial.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    days_left = None
    if trial and trial.expires_at:
        exp = trial.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days_left = max(0, (exp - datetime.now(timezone.utc)).days)
    return "trial", days_left


async def _get_owned_child_agent(
    child_agent_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession
) -> ChildAgent:
    result = await db.execute(
        select(ChildAgent)
        .join(ChildAgent.business)
        .where(
            ChildAgent.id == child_agent_id,
            Business.owner_id == user_id,
        )
    )
    child = result.scalar_one_or_none()
    if child is None:
        raise NotFoundError("ChildAgent", str(child_agent_id))
    return child


async def _update_agent_rating(agent_id: uuid.UUID, db: AsyncSession) -> None:
    from sqlalchemy import func as sqlfunc
    result = await db.execute(
        select(
            sqlfunc.avg(AgentReview.rating).label("avg"),
            sqlfunc.count(AgentReview.id).label("count"),
        ).where(
            AgentReview.agent_id == agent_id,
            AgentReview.is_visible.is_(True),
        )
    )
    row = result.fetchone()
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent and row:
        agent.average_rating = float(row.avg) if row.avg else None
        agent.review_count = row.count or 0


def _agent_to_response(agent: Agent) -> AgentPublicResponse:
    return AgentPublicResponse(
        id=str(agent.id),
        name=agent.name,
        tagline=agent.tagline,
        description=agent.description,
        category=agent.category,
        tags=agent.tags or [],
        capabilities=agent.capabilities or [],
        child_schema=agent.child_schema,
        setup_guide=agent.setup_guide,
        price_etg=agent.price_etg,
        preferred_model_id=agent.preferred_model_id,
        status=agent.status.value,
        is_featured=agent.is_featured,
        is_staff_pick=agent.is_staff_pick,
        cover_image_url=agent.cover_image_url,
        total_unlocks=agent.total_unlocks,
        average_rating=agent.average_rating,
        review_count=agent.review_count,
        created_at=agent.created_at.isoformat(),
    )
