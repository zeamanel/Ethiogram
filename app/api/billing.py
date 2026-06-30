# app/api/billing.py
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.metering import metering_service
from app.db.models import (
    Business,
    EtgPackage,
    EtgTransaction,
    PaymentProvider,
    RechargeOrder,
    PaymentStatus,
    TokenWallet,
    UsageDailyAggregate,
    UsageEvent,
)
from app.db.session import get_db, get_redis

logger = get_logger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class WalletResponse(BaseModel):
    business_id: str
    balance: int
    escrow_balance: int
    lifetime_recharged: int
    lifetime_spent: int
    auto_recharge_enabled: bool
    auto_recharge_threshold: int
    auto_recharge_amount: int
    monthly_spend_limit: Optional[int]
    current_month_spend: int
    subscription_plan: str


class TransactionResponse(BaseModel):
    id: str
    amount: int
    balance_before: int
    balance_after: int
    transaction_type: str
    description: str
    created_at: str


class PackageResponse(BaseModel):
    id: str
    name: str
    etg_amount: int
    bonus_etg: int
    price_usd: float
    price_etb: float


class RechargeRequest(BaseModel):
    package_id: uuid.UUID
    payment_provider: str
    return_url: Optional[str] = None

    @field_validator("payment_provider")
    @classmethod
    def valid_provider(cls, v: str) -> str:
        valid = {p.value for p in PaymentProvider}
        if v not in valid:
            raise ValueError(f"Invalid provider. Choose from: {valid}")
        return v


class RechargeResponse(BaseModel):
    order_id: str
    payment_url: Optional[str]
    etg_amount: int
    bonus_etg: int
    fiat_amount: float
    fiat_currency: str
    status: str


class AutoRechargeRequest(BaseModel):
    enabled: bool
    threshold: Optional[int] = None
    amount: Optional[int] = None
    provider: Optional[str] = None


class UsageSummaryResponse(BaseModel):
    period: str
    total_events: int
    total_etg: int
    total_input_tokens: int
    total_output_tokens: int
    breakdown: list[dict]


# ---------------------------------------------------------------------------
# Wallet endpoints
# ---------------------------------------------------------------------------

@router.get("/wallet/{business_id}", response_model=WalletResponse)
async def get_wallet(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> WalletResponse:
    await _assert_owns_business(current_user.id, business_id, db)

    result = await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == business_id)
    )
    wallet = result.scalar_one_or_none()
    if wallet is None:
        raise NotFoundError("Wallet", str(business_id))

    return WalletResponse(
        business_id=str(wallet.business_id),
        balance=wallet.balance,
        escrow_balance=wallet.escrow_balance,
        lifetime_recharged=wallet.lifetime_recharged,
        lifetime_spent=wallet.lifetime_spent,
        auto_recharge_enabled=wallet.auto_recharge_enabled,
        auto_recharge_threshold=wallet.auto_recharge_threshold,
        auto_recharge_amount=wallet.auto_recharge_amount,
        monthly_spend_limit=wallet.monthly_spend_limit,
        current_month_spend=wallet.current_month_spend,
        subscription_plan=wallet.subscription_plan.value,
    )


@router.get("/wallet/{business_id}/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> list[TransactionResponse]:
    await _assert_owns_business(current_user.id, business_id, db)

    wallet_result = await db.execute(
        select(TokenWallet.id).where(TokenWallet.business_id == business_id)
    )
    wallet_id = wallet_result.scalar_one_or_none()
    if wallet_id is None:
        return []

    result = await db.execute(
        select(EtgTransaction)
        .where(EtgTransaction.wallet_id == wallet_id)
        .order_by(EtgTransaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    txns = result.scalars().all()

    return [
        TransactionResponse(
            id=str(t.id),
            amount=t.amount,
            balance_before=t.balance_before,
            balance_after=t.balance_after,
            transaction_type=t.transaction_type,
            description=t.description,
            created_at=t.created_at.isoformat(),
        )
        for t in txns
    ]


@router.patch("/wallet/{business_id}/auto-recharge", status_code=204)
async def update_auto_recharge(
    business_id: uuid.UUID,
    body: AutoRechargeRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    await _assert_owns_business(current_user.id, business_id, db)

    result = await db.execute(
        select(TokenWallet).where(TokenWallet.business_id == business_id)
    )
    wallet = result.scalar_one_or_none()
    if wallet is None:
        raise NotFoundError("Wallet", str(business_id))

    wallet.auto_recharge_enabled = body.enabled
    if body.threshold is not None:
        if body.threshold < 0:
            raise ValidationError("Threshold must be >= 0")
        wallet.auto_recharge_threshold = body.threshold
    if body.amount is not None:
        if body.amount <= 0:
            raise ValidationError("Recharge amount must be > 0")
        wallet.auto_recharge_amount = body.amount
    if body.provider is not None:
        wallet.auto_recharge_provider = body.provider


# ---------------------------------------------------------------------------
# Packages & Recharge
# ---------------------------------------------------------------------------

@router.get("/packages", response_model=list[PackageResponse])
async def list_packages(db: AsyncSession = Depends(get_db)) -> list[PackageResponse]:
    result = await db.execute(
        select(EtgPackage)
        .where(EtgPackage.is_active.is_(True))
        .order_by(EtgPackage.display_order)
    )
    packages = result.scalars().all()
    return [
        PackageResponse(
            id=str(p.id),
            name=p.name,
            etg_amount=p.etg_amount,
            bonus_etg=p.bonus_etg,
            price_usd=p.price_usd,
            price_etb=p.price_etb,
        )
        for p in packages
    ]


@router.post("/recharge/{business_id}", response_model=RechargeResponse, status_code=201)
async def initiate_recharge(
    business_id: uuid.UUID,
    body: RechargeRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RechargeResponse:
    await _assert_owns_business(current_user.id, business_id, db)

    pkg_result = await db.execute(
        select(EtgPackage).where(
            EtgPackage.id == body.package_id,
            EtgPackage.is_active.is_(True),
        )
    )
    pkg = pkg_result.scalar_one_or_none()
    if pkg is None:
        raise NotFoundError("Package", str(body.package_id))

    provider = PaymentProvider(body.payment_provider)
    fiat_amount, fiat_currency = _resolve_fiat(pkg, provider)

    order = RechargeOrder(
        business_id=business_id,
        etg_package_id=pkg.id,
        etg_amount=pkg.etg_amount,
        fiat_amount=fiat_amount,
        fiat_currency=fiat_currency,
        payment_provider=provider,
        status=PaymentStatus.pending,
        bonus_etg=pkg.bonus_etg,
    )
    db.add(order)
    await db.flush()

    payment_url = await _get_payment_url(
        order, body.return_url,
        email=current_user.email, first_name=current_user.full_name)

    logger.info(
        "Recharge initiated",
        order_id=str(order.id),
        business_id=str(business_id),
        etg=pkg.etg_amount,
        provider=provider.value,
    )

    return RechargeResponse(
        order_id=str(order.id),
        payment_url=payment_url,
        etg_amount=pkg.etg_amount,
        bonus_etg=pkg.bonus_etg,
        fiat_amount=fiat_amount,
        fiat_currency=fiat_currency,
        status="pending",
    )


@router.post("/webhook/chapa", include_in_schema=False)
async def chapa_webhook(
    request_data: dict,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> dict:
    """Chapa payment confirmation webhook.

    The callback body is NOT trusted — we re-verify the transaction directly with
    Chapa before crediting, so a forged webhook can't top up a wallet. Idempotent:
    a tx_ref whose order is already completed is a no-op.
    """
    tx_ref = request_data.get("tx_ref") or request_data.get("trx_ref")
    if not tx_ref:
        return {"ok": True}

    result = await db.execute(
        select(RechargeOrder).where(RechargeOrder.payment_reference == tx_ref)
    )
    order = result.scalar_one_or_none()
    if order is None or order.status != PaymentStatus.pending:
        return {"ok": True}   # unknown ref or already processed

    # Confirm with Chapa server-to-server (don't trust the webhook body).
    from app.services.chapa_service import chapa_service
    verified = await chapa_service.verify(tx_ref)
    if verified is None:
        logger.warning("Chapa webhook unverified — not crediting", tx_ref=tx_ref)
        return {"ok": True}
    try:
        if float(verified.get("amount", 0)) + 0.01 < float(order.fiat_amount):
            logger.error("Chapa amount mismatch — not crediting", tx_ref=tx_ref,
                         paid=verified.get("amount"), expected=order.fiat_amount)
            return {"ok": True}
    except (TypeError, ValueError):
        pass

    from datetime import datetime, timezone
    order.status = PaymentStatus.completed
    order.completed_at = datetime.now(timezone.utc)
    order.webhook_payload = request_data
    total_etg = order.etg_amount + order.bonus_etg

    await metering_service.credit(
        business_id=str(order.business_id),
        amount=total_etg,
        description=f"Chapa recharge — {total_etg} ETG",
        redis=redis,
        db=db,
        reference_type="recharge",
        reference_id=str(order.id),
    )
    await metering_service.reactivate_grace_bots(order.business_id, db)
    logger.info("Chapa recharge completed", order_id=str(order.id), etg=total_etg)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Usage analytics
# ---------------------------------------------------------------------------

@router.get("/usage/{business_id}", response_model=UsageSummaryResponse)
async def get_usage_summary(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    period: str = Query("7d", pattern=r"^\d+[dh]$"),
) -> UsageSummaryResponse:
    await _assert_owns_business(current_user.id, business_id, db)

    from datetime import timedelta
    unit = period[-1]
    value = int(period[:-1])
    delta = timedelta(days=value) if unit == "d" else timedelta(hours=value)
    from datetime import datetime, timezone
    since = datetime.now(timezone.utc) - delta

    result = await db.execute(
        select(
            UsageEvent.action_type,
            func.count(UsageEvent.id).label("events"),
            func.sum(UsageEvent.etg_charged).label("etg"),
            func.sum(UsageEvent.input_tokens).label("input_tokens"),
            func.sum(UsageEvent.output_tokens).label("output_tokens"),
        )
        .where(
            UsageEvent.business_id == str(business_id),
            UsageEvent.created_at >= since,
        )
        .group_by(UsageEvent.action_type)
    )
    rows = result.fetchall()

    breakdown = [
        {
            "action_type": row.action_type,
            "events": row.events,
            "etg": int(row.etg or 0),
            "input_tokens": int(row.input_tokens or 0),
            "output_tokens": int(row.output_tokens or 0),
        }
        for row in rows
    ]

    return UsageSummaryResponse(
        period=period,
        total_events=sum(r["events"] for r in breakdown),
        total_etg=sum(r["etg"] for r in breakdown),
        total_input_tokens=sum(r["input_tokens"] for r in breakdown),
        total_output_tokens=sum(r["output_tokens"] for r in breakdown),
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _assert_owns_business(
    user_id: uuid.UUID, business_id: uuid.UUID, db: AsyncSession
) -> None:
    result = await db.execute(
        select(Business.id).where(
            Business.id == business_id,
            Business.owner_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Business", str(business_id))


def _resolve_fiat(pkg: EtgPackage, provider: PaymentProvider) -> tuple[float, str]:
    if provider in (PaymentProvider.chapa, PaymentProvider.telebirr):
        return pkg.price_etb, "ETB"
    return pkg.price_usd, "USD"


async def _get_payment_url(
    order: RechargeOrder, return_url: Optional[str], *,
    email: Optional[str] = None, first_name: Optional[str] = None,
) -> Optional[str]:
    """Create the provider checkout and return its URL. Sets the order's
    payment_reference (the tx_ref the webhook matches on)."""
    if order.payment_provider == PaymentProvider.chapa:
        from app.services.chapa_service import chapa_service
        tx_ref = f"etg-{order.id}"
        order.payment_reference = tx_ref
        callback_url = (settings.base_url.rstrip("/") + settings.api_prefix
                        + "/billing/webhook/chapa")
        return await chapa_service.initialize(
            amount=order.fiat_amount, currency=order.fiat_currency, tx_ref=tx_ref,
            email=email, first_name=first_name,
            callback_url=callback_url, return_url=return_url,
            meta={"platform": "ethiogram", "business_id": str(order.business_id)})
    # Other providers (telebirr / paypal) not wired yet.
    return None
