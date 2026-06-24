# app/api/businesses.py
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.logging import get_logger
from app.db.models import Business
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
