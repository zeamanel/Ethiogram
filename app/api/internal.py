# app/api/internal.py
"""
Service-to-service endpoint used by Odaflux (musa-ai-backend) to credit an
Ethiogram business wallet after a Chapa payment is verified on that side.

Protected by a shared secret: the caller must present the same
CHAPA_INTERNAL_KEY value in the X-Internal-Key header. The endpoint is
excluded from the public OpenAPI schema.

Route:  POST /api/v1/internal/chapa-credit
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metering import metering_service
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


class ChapaCreditPayload(BaseModel):
    user_id: str
    business_id: str
    amount: int
    tx_ref: str
    chapa_data: dict = {}


@router.post("/chapa-credit", include_in_schema=False)
async def internal_chapa_credit(
    payload: ChapaCreditPayload,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    x_internal_key: str = Header(default=""),
) -> dict:
    """Credit a business wallet after Chapa payment verified by Odaflux.

    Expected JSON body:
        user_id      str   — ID of the user who initiated the payment
        business_id  str   — UUID of the business to credit
        amount       int   — ETG tokens to add (positive integer)
        tx_ref       str   — Chapa tx_ref for audit trail
        chapa_data   dict  — raw Chapa payload metadata

    Returns {"status": "ok", "new_balance": <int>}.
    """
    if not settings.chapa_internal_key:
        raise HTTPException(status_code=500, detail="CHAPA_INTERNAL_KEY not configured")
    if x_internal_key != settings.chapa_internal_key:
        raise HTTPException(status_code=401, detail="Invalid or missing internal key")

    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be a positive integer (ETG tokens)")

    new_balance = await metering_service.credit(
        business_id=payload.business_id,
        amount=payload.amount,
        description=(
            f"Chapa recharge via Odaflux — user {payload.user_id} ref {payload.tx_ref}"
            if payload.tx_ref else "Chapa recharge via Odaflux"
        ),
        redis=redis,
        db=db,
        reference_type="recharge",
        reference_id=payload.tx_ref,
    )
    await metering_service.reactivate_grace_bots(payload.business_id, db)

    logger.info(
        "Internal Chapa credit applied",
        user_id=payload.user_id,
        business_id=payload.business_id,
        amount=payload.amount,
        new_balance=new_balance,
        tx_ref=payload.tx_ref,
        chapa_data=payload.chapa_data,
    )
    return {"status": "ok", "new_balance": new_balance}
