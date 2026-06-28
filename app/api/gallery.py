# app/api/gallery.py
"""Bot Gallery — a PUBLIC directory of businesses for customers to discover.

No auth (anyone can browse). Lists active businesses that have a connected,
active bot (so the "Chat" deep link works), with search + category filter.
Each entry deep-links to the bot (t.me/{username}) and to the storefront.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Bot, BotStatus, Business
from app.db.session import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/gallery", tags=["gallery"])


def _active_bot_username():
    """Scalar subquery → the business's active bot username (None if none)."""
    return (select(Bot.bot_username)
            .where(Bot.business_id == Business.id,
                   Bot.status == BotStatus.active,
                   Bot.bot_username.is_not(None))
            .order_by(Bot.created_at.asc()).limit(1)
            .correlate(Business).scalar_subquery())


class GalleryItem(BaseModel):
    name: str
    slug: str
    category: Optional[str]
    tagline: Optional[str]
    logo_url: Optional[str]
    bot_username: str
    store_url: str
    chat_url: str


class GalleryList(BaseModel):
    items: list[GalleryItem]
    total: int
    limit: int
    offset: int


@router.get("", response_model=GalleryList)
async def list_gallery(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(24, ge=1, le=60),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> GalleryList:
    bu = _active_bot_username()
    stmt = select(Business, bu.label("bot_username")).where(
        Business.deleted_at.is_(None),
        Business.is_suspended.is_(False),
        bu.is_not(None),                       # only businesses with a reachable bot
    )
    if category:
        stmt = stmt.where(Business.category == category)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            Business.name.ilike(like) | Business.category.ilike(like) | Business.description.ilike(like)
        )

    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(
        stmt.order_by(Business.name.asc()).limit(limit).offset(offset))).all()

    items = [GalleryItem(
        name=b.name, slug=b.slug, category=b.category,
        tagline=b.description, logo_url=b.logo_url, bot_username=username,
        store_url=f"/app/store/?s={b.slug}",
        chat_url=f"https://t.me/{username}",
    ) for (b, username) in rows]
    return GalleryList(items=items, total=total, limit=limit, offset=offset)


@router.get("/categories", response_model=list[str])
async def list_categories(db: AsyncSession = Depends(get_db)) -> list[str]:
    """Distinct categories of listed (active, bot-connected) businesses."""
    bu = _active_bot_username()
    rows = (await db.execute(
        select(Business.category)
        .where(Business.deleted_at.is_(None), Business.is_suspended.is_(False),
               Business.category.is_not(None), bu.is_not(None))
        .distinct().order_by(Business.category.asc())
    )).scalars().all()
    return [c for c in rows if c]
