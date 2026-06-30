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

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metering import metering_service
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/chapa-credit", include_in_schema=False)
async def internal_chapa_credit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    x_internal_key: str = Header(default=""),
) -> dict:
    """Credit a business wallet after Chapa payment verified by Odaflux.

    Expected JSON body:
        business_id  str   — UUID of the business to credit
        amount       int   — ETG tokens to add (positive integer)
        tx_ref       str   — Chapa tx_ref for audit trail (optional)

    Returns {"status": "ok", "new_balance": <int>}.
    """
    if not settings.chapa_internal_key or x_internal_key != settings.chapa_internal_key:
        raise HTTPException(status_code=401, detail="Invalid or missing internal key")

    data = await request.json()
    business_id: Optional[str] = data.get("business_id")
    amount = data.get("amount")
    tx_ref: str = str(data.get("tx_ref") or "")

    if not business_id:
        raise HTTPException(status_code=400, detail="Missing business_id")
    if not isinstance(amount, int) or amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be a positive integer (ETG tokens)")

    new_balance = await metering_service.credit(
        business_id=business_id,
        amount=amount,
        description=f"Chapa recharge via Odaflux — ref {tx_ref}" if tx_ref else "Chapa recharge via Odaflux",
        redis=redis,
        db=db,
        reference_type="recharge",
        reference_id=tx_ref or None,
    )
    await metering_service.reactivate_grace_bots(business_id, db)

    logger.info(
        "Internal Chapa credit applied",
        business_id=business_id,
        amount=amount,
        new_balance=new_balance,
        tx_ref=tx_ref,
    )
    return {"status": "ok", "new_balance": new_balance}
