# app/api/transfers.py
"""Business ownership transfer — My Bots → "Transfer business".

An owner hands a business to another person by Telegram username, Telegram id,
or email. It's a two-step, consent-based flow: the owner creates a PENDING
transfer (reversible), and ownership only moves when the recipient ACCEPTS from
their own dashboard. This protects against typos, and works even if the
recipient hasn't signed up yet (the row waits until their identity matches).
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.db.models import (
    TRANSFER_KINDS, Business, BusinessTransfer, User,
)
from app.db.session import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/transfers", tags=["transfers"])

_TTL_DAYS = 7


# ── helpers ──────────────────────────────────────────────────────────────────

def _normalize(kind: str, value: str) -> str:
    value = (value or "").strip()
    if kind == "telegram_username":
        v = value.lstrip("@").lower()
        if not v:
            raise ValidationError("Enter a Telegram username.")
        return v
    if kind == "telegram_id":
        if not value.isdigit():
            raise ValidationError("Telegram ID must be a number.")
        return value
    if kind == "email":
        v = value.lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValidationError("Enter a valid email address.")
        return v
    raise ValidationError("Unknown recipient type.")


def _own_value(user: User, kind: str) -> Optional[str]:
    if kind == "telegram_id":
        return str(user.telegram_id) if user.telegram_id else None
    if kind == "telegram_username":
        return user.username.lower() if user.username else None
    if kind == "email":
        return user.email.lower() if user.email else None
    return None


def _user_matches(user: User, t: BusinessTransfer) -> bool:
    return _own_value(user, t.to_kind) == t.to_value


async def _resolve_target(kind: str, value: str, db: AsyncSession) -> Optional[User]:
    if kind == "telegram_id":
        return (await db.execute(select(User).where(User.telegram_id == int(value)))).scalar_one_or_none()
    if kind == "telegram_username":
        return (await db.execute(select(User).where(func.lower(User.username) == value))).scalar_one_or_none()
    if kind == "email":
        return (await db.execute(select(User).where(func.lower(User.email) == value))).scalar_one_or_none()
    return None


async def _owned(business_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Business:
    biz = (await db.execute(select(Business).where(
        Business.id == business_id, Business.owner_id == user_id,
        Business.is_suspended.is_(False), Business.deleted_at.is_(None),
    ))).scalar_one_or_none()
    if biz is None:
        raise NotFoundError("Business", str(business_id))
    return biz


async def _pending_for(business_id: uuid.UUID, db: AsyncSession) -> Optional[BusinessTransfer]:
    return (await db.execute(select(BusinessTransfer).where(
        BusinessTransfer.business_id == business_id,
        BusinessTransfer.status == "pending",
    ).order_by(BusinessTransfer.created_at.desc()))).scalars().first()


async def _notify_target(target: Optional[User], business: Business, from_user: User) -> None:
    if target is None or not target.telegram_id or not settings.master_bot_token:
        return
    try:
        from app.services.telegram_service import telegram_service
        who = from_user.full_name or (f"@{from_user.username}" if from_user.username else "An owner")
        await telegram_service.send_message(
            settings.master_bot_token, target.telegram_id,
            f"🤝 {who} wants to transfer the business <b>{business.name}</b> to you on "
            f"Ethiogram.\nOpen the app (My Account → Incoming transfers) to accept or decline.")
    except Exception as exc:
        logger.warning("Transfer notification failed", error=str(exc))


# ── schemas ──────────────────────────────────────────────────────────────────

class TransferRequest(BaseModel):
    to_kind: str
    to_value: str


class TransferStatus(BaseModel):
    status: str                       # "none" | "pending"
    id: Optional[str] = None
    to_kind: Optional[str] = None
    to_value: Optional[str] = None
    recipient_has_account: bool = False
    expires_at: Optional[str] = None


class IncomingTransfer(BaseModel):
    id: str
    business_id: str
    business_name: str
    from_name: Optional[str]
    expires_at: str


def _status(t: Optional[BusinessTransfer]) -> TransferStatus:
    if t is None:
        return TransferStatus(status="none")
    return TransferStatus(
        status="pending", id=str(t.id), to_kind=t.to_kind, to_value=t.to_value,
        recipient_has_account=t.to_user_id is not None, expires_at=t.expires_at.isoformat())


# ── owner side ───────────────────────────────────────────────────────────────

@router.get("/business/{business_id}", response_model=TransferStatus)
async def get_transfer(
    business_id: uuid.UUID, current_user: CurrentUser, db: AsyncSession = Depends(get_db),
) -> TransferStatus:
    await _owned(business_id, current_user.id, db)
    return _status(await _pending_for(business_id, db))


@router.post("/business/{business_id}", response_model=TransferStatus)
async def initiate_transfer(
    business_id: uuid.UUID, body: TransferRequest, current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> TransferStatus:
    """Create (or replace) a pending transfer of this business to someone else."""
    business = await _owned(business_id, current_user.id, db)
    if body.to_kind not in TRANSFER_KINDS:
        raise ValidationError("Choose Telegram username, Telegram ID, or email.")
    value = _normalize(body.to_kind, body.to_value)
    if _own_value(current_user, body.to_kind) == value:
        raise ValidationError("You already own this business.")

    target = await _resolve_target(body.to_kind, value, db)
    if target is not None and target.id == current_user.id:
        raise ValidationError("You already own this business.")

    existing = await _pending_for(business_id, db)
    if existing is not None:                 # one pending transfer at a time
        existing.status = "cancelled"
        existing.resolved_at = datetime.now(timezone.utc)

    t = BusinessTransfer(
        business_id=business.id, from_user_id=current_user.id,
        to_kind=body.to_kind, to_value=value,
        to_user_id=(target.id if target else None), status="pending",
        token=secrets.token_urlsafe(24)[:64],
        expires_at=datetime.now(timezone.utc) + timedelta(days=_TTL_DAYS),
    )
    db.add(t)
    await db.flush()
    await _notify_target(target, business, current_user)
    logger.info("Business transfer initiated", business_id=str(business_id),
                to_kind=body.to_kind, recipient_known=target is not None)
    return _status(t)


@router.delete("/business/{business_id}", status_code=204)
async def cancel_transfer(
    business_id: uuid.UUID, current_user: CurrentUser, db: AsyncSession = Depends(get_db),
) -> Response:
    await _owned(business_id, current_user.id, db)
    t = await _pending_for(business_id, db)
    if t is not None:
        t.status = "cancelled"
        t.resolved_at = datetime.now(timezone.utc)
        await db.flush()
    return Response(status_code=204)


# ── recipient side ───────────────────────────────────────────────────────────

@router.get("/incoming", response_model=list[IncomingTransfer])
async def list_incoming(
    current_user: CurrentUser, db: AsyncSession = Depends(get_db),
) -> list[IncomingTransfer]:
    """Pending transfers addressed to the current user (by any of their ids)."""
    conds = []
    if current_user.telegram_id is not None:
        conds.append(and_(BusinessTransfer.to_kind == "telegram_id",
                          BusinessTransfer.to_value == str(current_user.telegram_id)))
    if current_user.username:
        conds.append(and_(BusinessTransfer.to_kind == "telegram_username",
                          BusinessTransfer.to_value == current_user.username.lower()))
    if current_user.email:
        conds.append(and_(BusinessTransfer.to_kind == "email",
                          BusinessTransfer.to_value == current_user.email.lower()))
    if not conds:
        return []

    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(BusinessTransfer, Business, User)
        .join(Business, BusinessTransfer.business_id == Business.id)
        .join(User, BusinessTransfer.from_user_id == User.id)
        .where(BusinessTransfer.status == "pending",
               BusinessTransfer.expires_at > now,
               Business.deleted_at.is_(None),
               or_(*conds))
        .order_by(BusinessTransfer.created_at.desc())
    )).all()
    return [
        IncomingTransfer(
            id=str(t.id), business_id=str(b.id), business_name=b.name,
            from_name=(u.full_name or (f"@{u.username}" if u.username else None)),
            expires_at=t.expires_at.isoformat())
        for (t, b, u) in rows
    ]


async def _load_incoming(transfer_id: uuid.UUID, current_user: User, db: AsyncSession) -> BusinessTransfer:
    t = (await db.execute(select(BusinessTransfer).where(
        BusinessTransfer.id == transfer_id))).scalar_one_or_none()
    if t is None or t.status != "pending":
        raise NotFoundError("Transfer", str(transfer_id))
    expires = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        t.status = "expired"
        raise ValidationError("This transfer has expired.")
    if not _user_matches(current_user, t):
        raise PermissionDeniedError("This transfer isn't addressed to you.")
    return t


@router.post("/incoming/{transfer_id}/accept")
async def accept_transfer(
    transfer_id: uuid.UUID, current_user: CurrentUser, db: AsyncSession = Depends(get_db),
) -> dict:
    """Accept a transfer — reassign ownership of the business to the current user."""
    t = await _load_incoming(transfer_id, current_user, db)
    business = (await db.execute(select(Business).where(
        Business.id == t.business_id, Business.deleted_at.is_(None)))).scalar_one_or_none()
    if business is None:
        raise NotFoundError("Business", str(t.business_id))

    business.owner_id = current_user.id
    now = datetime.now(timezone.utc)
    t.status = "accepted"
    t.resolved_at = now
    t.resolved_by_id = current_user.id
    t.to_user_id = current_user.id

    # Drop any other pending transfers for this business.
    others = (await db.execute(select(BusinessTransfer).where(
        BusinessTransfer.business_id == business.id,
        BusinessTransfer.status == "pending",
        BusinessTransfer.id != t.id))).scalars().all()
    for o in others:
        o.status = "cancelled"
        o.resolved_at = now
    await db.flush()
    logger.info("Business transfer accepted", business_id=str(business.id),
                new_owner=str(current_user.id))
    return {"ok": True, "business_id": str(business.id), "business_name": business.name}


@router.post("/incoming/{transfer_id}/decline", status_code=204)
async def decline_transfer(
    transfer_id: uuid.UUID, current_user: CurrentUser, db: AsyncSession = Depends(get_db),
) -> Response:
    t = await _load_incoming(transfer_id, current_user, db)
    t.status = "declined"
    t.resolved_at = datetime.now(timezone.utc)
    t.resolved_by_id = current_user.id
    await db.flush()
    return Response(status_code=204)
