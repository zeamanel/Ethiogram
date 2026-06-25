# app/api/businesses.py
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import Business, BusinessBrainConfig
from app.db.session import get_db
from app.utils.text import slugify

logger = get_logger(__name__)

router = APIRouter(prefix="/businesses", tags=["businesses"])


class CreateBusinessRequest(BaseModel):
    name: str
    category: Optional[str] = None
    description: Optional[str] = None
    country_code: Optional[str] = None     # falls back to model default (ET)
    timezone: Optional[str] = None         # falls back to model default (Africa/Addis_Ababa)
    currency: Optional[str] = None         # falls back to model default (ETB)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 2:
            raise ValueError("Business name must be at least 2 characters")
        if len(v) > 255:
            raise ValueError("Business name too long")
        return v


class BusinessResponse(BaseModel):
    id: str
    name: str
    slug: str
    category: Optional[str]


async def _unique_slug(name: str, db: AsyncSession) -> str:
    base = slugify(name) or "business"
    slug, n = base, 1
    while await db.scalar(select(Business.id).where(Business.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    return slug


@router.post("", response_model=BusinessResponse, status_code=201)
async def create_business(
    body: CreateBusinessRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BusinessResponse:
    """
    Create a business owned by the current user (onboarding step 1). The wallet,
    brain config, and ETG signup bonus are created when the owner connects their
    first bot via POST /bots.
    """
    fields = {"owner_id": current_user.id, "name": body.name, "slug": await _unique_slug(body.name, db)}
    if body.category:      fields["category"] = body.category
    if body.description:   fields["description"] = body.description
    if body.country_code:  fields["country_code"] = body.country_code
    if body.timezone:      fields["timezone"] = body.timezone
    if body.currency:      fields["currency"] = body.currency

    business = Business(**fields)
    db.add(business)
    await db.flush()
    logger.info("Business created", business_id=str(business.id),
                owner_id=str(current_user.id), slug=business.slug)
    return BusinessResponse(
        id=str(business.id), name=business.name, slug=business.slug, category=business.category,
    )


# ---------------------------------------------------------------------------
# Business Brain settings (persona, tone, fallback, RAG tuning)
# ---------------------------------------------------------------------------

class BrainConfigResponse(BaseModel):
    persona_name: str
    persona_tone: str
    system_prompt_extra: Optional[str]
    fallback_message: str
    handoff_message: str
    out_of_hours_message: Optional[str]
    rag_top_k: int
    rag_similarity_threshold: float
    max_history_messages: int
    collect_customer_name: bool
    collect_customer_phone: bool


class BrainConfigUpdate(BaseModel):
    persona_name: Optional[str] = None
    persona_tone: Optional[str] = None
    system_prompt_extra: Optional[str] = None
    fallback_message: Optional[str] = None
    handoff_message: Optional[str] = None
    out_of_hours_message: Optional[str] = None
    rag_top_k: Optional[int] = None
    rag_similarity_threshold: Optional[float] = None
    max_history_messages: Optional[int] = None
    collect_customer_name: Optional[bool] = None
    collect_customer_phone: Optional[bool] = None

    @field_validator("rag_similarity_threshold")
    @classmethod
    def _thresh(cls, v):
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("rag_similarity_threshold must be between 0.0 and 1.0")
        return v

    @field_validator("rag_top_k")
    @classmethod
    def _topk(cls, v):
        if v is not None and not (1 <= v <= 20):
            raise ValueError("rag_top_k must be between 1 and 20")
        return v

    @field_validator("max_history_messages")
    @classmethod
    def _hist(cls, v):
        if v is not None and not (1 <= v <= 100):
            raise ValueError("max_history_messages must be between 1 and 100")
        return v

    @field_validator("persona_name")
    @classmethod
    def _persona(cls, v):
        if v is not None:
            v = v.strip()
            if not (1 <= len(v) <= 128):
                raise ValueError("persona_name must be 1-128 characters")
        return v


async def _get_owned_business(business_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Business:
    result = await db.execute(
        select(Business).where(Business.id == business_id, Business.owner_id == user_id)
    )
    business = result.scalar_one_or_none()
    if business is None:
        raise NotFoundError("Business", str(business_id))
    return business


def _brain_to_response(c: BusinessBrainConfig) -> BrainConfigResponse:
    # A non-persisted config (defaults path) has None attrs until flush, so fall
    # back to each column's declared default.
    cols = BusinessBrainConfig.__table__.c

    def val(name):
        v = getattr(c, name)
        if v is None and cols[name].default is not None:
            return cols[name].default.arg
        return v

    return BrainConfigResponse(
        persona_name=val("persona_name"), persona_tone=val("persona_tone"),
        system_prompt_extra=val("system_prompt_extra"), fallback_message=val("fallback_message"),
        handoff_message=val("handoff_message"), out_of_hours_message=val("out_of_hours_message"),
        rag_top_k=val("rag_top_k"), rag_similarity_threshold=val("rag_similarity_threshold"),
        max_history_messages=val("max_history_messages"),
        collect_customer_name=val("collect_customer_name"),
        collect_customer_phone=val("collect_customer_phone"),
    )


@router.get("/{business_id}/brain", response_model=BrainConfigResponse)
async def get_brain_config(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BrainConfigResponse:
    await _get_owned_business(business_id, current_user.id, db)
    config = (await db.execute(
        select(BusinessBrainConfig).where(BusinessBrainConfig.business_id == business_id)
    )).scalar_one_or_none()
    if config is None:
        # No config yet (business without a bot) — return model defaults to edit.
        config = BusinessBrainConfig(business_id=business_id)
    return _brain_to_response(config)


@router.patch("/{business_id}/brain", response_model=BrainConfigResponse)
async def update_brain_config(
    business_id: uuid.UUID,
    body: BrainConfigUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BrainConfigResponse:
    """Edit the Business Brain (persona, tone, fallback, RAG tuning). Upserts the
    config if the business doesn't have one yet (e.g. no bot connected)."""
    await _get_owned_business(business_id, current_user.id, db)
    config = (await db.execute(
        select(BusinessBrainConfig).where(BusinessBrainConfig.business_id == business_id)
    )).scalar_one_or_none()

    changes = body.model_dump(exclude_unset=True)
    if config is None:
        config = BusinessBrainConfig(business_id=business_id, **changes)
        db.add(config)
        await db.flush()
    else:
        for k, v in changes.items():
            setattr(config, k, v)

    logger.info("Brain config updated", business_id=str(business_id), fields=list(changes.keys()))
    return _brain_to_response(config)
