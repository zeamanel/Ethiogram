# Chapa Integration Review — Ethiogram

**Date:** July 3, 2026  
**Scope:** Recharge flow, webhook handler, service-to-service endpoint, and configuration

---

## 1. CONFIGURATION & ENV VARS

### ✅ Located in [app/core/config.py](app/core/config.py)

```python
# Lines 100-109
chapa_secret_key: Optional[str] = Field(
    default=None,
    validation_alias=AliasChoices("CHAPA_SECRET_KEY", "CHAPA_PAYMENT_TOKEN"),
)
chapa_public_key: Optional[str] = None
chapa_internal_key: str = ""   # shared secret for service-to-service calls (Odaflux)
chapa_base_url: str = "https://api.chapa.co/v1"
chapa_webhook_secret: Optional[str] = None
```

**Status:**
- ✅ `CHAPA_SECRET_KEY` supports alias `CHAPA_PAYMENT_TOKEN` (backward-compatible with older deployments)
- ✅ `chapa_internal_key` defined for Odaflux service-to-service calls
- ✅ `chapa_base_url` configurable with sensible default
- ⚠️ **ISSUE #1:** `chapa_webhook_secret` is defined but **NEVER USED** in webhook validation
  - Config exists at line 109 but not referenced in billing.py webhook handler

---

## 2. CHAPA SERVICE — Payment API Wrapper

### Located in [app/services/chapa_service.py](app/services/chapa_service.py)

#### `initialize()` — Create transaction for hosted checkout
**Lines 27–55:**
```python
async def initialize(
    self, *, amount, currency, tx_ref: str, email: Optional[str],
    first_name: Optional[str], callback_url: str, return_url: Optional[str] = None,
    meta: Optional[dict] = None,
) -> Optional[str]:
```

**Status:**
- ✅ Includes `meta` parameter for custom data (platform, business_id)
- ✅ Graceful fallback: returns `None` if `chapa_secret_key` not configured (safe no-op)
- ✅ Proper error handling: logs and returns `None` on API failures
- ✅ Validates response structure: checks both `status == "success"` and extracts `data.checkout_url`

#### `verify()` — Server-to-server confirmation
**Lines 57–72:**
```python
async def verify(self, tx_ref: str) -> Optional[dict]:
    """Verify a transaction with Chapa. Returns the data dict only when the
    payment is confirmed successful; None otherwise (incl. pending/failed)."""
```

**Status:**
- ✅ Only returns transaction data when **both conditions** are met:
  1. Response status = "success" (outer)
  2. Inner `data.status = "success"` (inner)
- ✅ Returns `None` for pending/failed/exceptions (safe default)
- ✅ Used by webhook to prevent spoofed callbacks

---

## 3. RECHARGE INITIATION — Meta Object

### Located in [app/api/billing.py:initiate_recharge()](app/api/billing.py#L240)

**Lines 240–288:**
```python
@router.post("/recharge/{business_id}", response_model=RechargeResponse, status_code=201)
async def initiate_recharge(
    business_id: uuid.UUID,
    body: RechargeRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> RechargeResponse:
```

### Payment URL Creation — [_get_payment_url() helper](app/api/billing.py#L432)
**Lines 432–454:**
```python
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
```

**Status:**
- ✅ **Meta object correct:** includes both `platform: "ethiogram"` and `business_id`
- ✅ `tx_ref` format: `etg-{order.id}` — namespaced and unique per order
- ✅ Stored in `order.payment_reference` for webhook lookup
- ✅ Callback URL properly constructed from config
- ✅ Idempotency: if no `chapa_secret_key`, returns `None` gracefully

---

## 4. WEBHOOK HANDLER — CRITICAL ISSUES

### Located in [app/api/billing.py:chapa_webhook()](app/api/billing.py#L296)

**Lines 296–351:**
```python
@router.post("/webhook/chapa", include_in_schema=False)
async def chapa_webhook(
    request_data: dict,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> dict:
```

### ❌ ISSUE #2: Invalid FastAPI Parameter Binding

**Problem:**
```python
async def chapa_webhook(
    request_data: dict,  # ← NO Body() annotation
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> dict:
    tx_ref = request_data.get("tx_ref") or request_data.get("trx_ref")
```

**Analysis:**
- In FastAPI, `request_data: dict` **without** `Body()` is invalid for JSON payloads
- FastAPI will attempt to treat it as a query parameter dict, not the JSON body
- The webhook code calls `.get()` on `request_data` expecting it to be the JSON body
- **Result:** Webhook likely receives empty dict, causing `tx_ref` lookup to fail silently

**Fix Required:**
```python
from fastapi import Body, Request
# Either:
async def chapa_webhook(
    request_data: dict = Body(...),  # Explicit Body annotation
    ...
) -> dict:

# Or (more robust):
async def chapa_webhook(
    request: Request,
    ...
) -> dict:
    request_data = await request.json()
    ...
```

---

### ❌ ISSUE #3: Missing Webhook Signature Validation

**Problem:**
- Config has `chapa_webhook_secret: Optional[str]` (line 109 in config.py)
- Webhook handler **never validates** this secret
- Unlike Telegram webhook (`_verify_signature()` in webhooks.py), Chapa webhook is unprotected
- Attacker can post arbitrary JSON to `/api/v1/billing/webhook/chapa` and credit any business

**Current Code:**
```python
def chapa_webhook(request_data: dict, ...):
    # NO signature validation!
    tx_ref = request_data.get("tx_ref")
```

**Comparison to Telegram webhook** (webhooks.py:168):
```python
def _verify_signature(secret: str, provided: str) -> bool:
    """Verify constant-time comparison of secret to header."""
    if not provided:
        return False
    return hmac.compare_digest(secret, provided)
```

**Chapa Protection Strategy:**
- Per Chapa docs, verify the webhook using HMAC-SHA256 on the request body
- Header should contain the signature (varies by Chapa implementation)
- **Workaround Currently in Place:** Server-to-server re-verification (`chapa_service.verify()`)
  - Forces re-check with Chapa API before crediting
  - Provides safety but at the cost of extra API call per webhook

**Recommendation:**
```python
async def chapa_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> dict:
    body_bytes = await request.body()
    signature_header = request.headers.get("X-Chapa-Signature", "")
    
    # Validate HMAC-SHA256 signature if secret is configured
    if settings.chapa_webhook_secret:
        expected = hmac.new(
            settings.chapa_webhook_secret.encode(),
            body_bytes,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature_header):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    request_data = json.loads(body_bytes)
    ...
```

---

### ✅ Idempotency & Re-verification

**Lines 308–323:**
```python
result = await db.execute(
    select(RechargeOrder).where(RechargeOrder.payment_reference == tx_ref)
)
order = result.scalar_one_or_none()
if order is None or order.status != PaymentStatus.pending:
    return {"ok": True}   # unknown ref or already processed

# Confirm with Chapa server-to-server (don't trust the webhook body).
verified = await chapa_service.verify(tx_ref)
if verified is None:
    logger.warning("Chapa webhook unverified — not crediting", tx_ref=tx_ref)
    return {"ok": True}
```

**Status:**
- ✅ **Doubled verification:** checks DB first, then re-verifies with Chapa API
- ✅ **Idempotent:** if order already completed, returns early (no duplicate credit)
- ✅ **Safety-first:** spoofed webhooks fail the Chapa verification check
- ⚠️ **Performance cost:** extra API call per webhook (acceptable trade-off for security)

---

### ✅ Amount Validation

**Lines 324–329:**
```python
try:
    if float(verified.get("amount", 0)) + 0.01 < float(order.fiat_amount):
        logger.error("Chapa amount mismatch — not crediting", tx_ref=tx_ref,
                     paid=verified.get("amount"), expected=order.fiat_amount)
        return {"ok": True}
except (TypeError, ValueError):
    pass
```

**Status:**
- ✅ Compares verified amount from Chapa to order fiat_amount
- ✅ Tolerance of ±0.01 (handles currency rounding)
- ✅ Silent pass-through on type errors (doesn't fail the entire webhook)

---

## 5. SERVICE-TO-SERVICE ENDPOINT — Odaflux Contract

### Located in [app/api/internal.py:internal_chapa_credit()](app/api/internal.py#L24)

**Lines 24–72:**
```python
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
```

### ✅ Authentication: X-Internal-Key Header

**Lines 43–44:**
```python
if not settings.chapa_internal_key or x_internal_key != settings.chapa_internal_key:
    raise HTTPException(status_code=401, detail="Invalid or missing internal key")
```

**Status:**
- ✅ Validates `X-Internal-Key` header against `settings.chapa_internal_key`
- ✅ Constant-time comparison: `!=` is sufficient since it's not cryptographic
- ⚠️ **ISSUE #4:** No HMAC-based signature on the request body itself
  - Only header-based shared secret (plus network-level HTTPS)
  - If musa-ai-backend (Odaflux) is compromised, any request with the shared key is valid
  - **Acceptable:** Assumes both services run in secured GCP environment with VPC isolation

### ✅ Request Validation

**Lines 46–52:**
```python
data = await request.json()
business_id: Optional[str] = data.get("business_id")
amount = data.get("amount")
tx_ref: str = str(data.get("tx_ref") or "")

if not business_id:
    raise HTTPException(status_code=400, detail="Missing business_id")
if not isinstance(amount, int) or amount <= 0:
    raise HTTPException(status_code=400, detail="amount must be a positive integer (ETG tokens)")
```

**Status:**
- ✅ Explicit type checks: `isinstance(amount, int)`
- ✅ Range check: `amount > 0` prevents negative credits
- ✅ business_id made optional in code but validated as required
- ✅ tx_ref optional (for audit trail), gracefully coerced to string

### ✅ Idempotency & Logging

**Lines 54–71:**
```python
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
```

**Status:**
- ✅ Logs all credit transactions with full context
- ✅ Returns new balance (allows Odaflux to verify idempotency)
- ✅ Reactivates grace bots after credit (same as webhook)
- ✅ Reference tracking: tx_ref stored for audit trail

### ✅ Payload Contract

**Expected by Odaflux:**
```json
{
  "business_id": "550e8400-e29b-41d4-a716-446655440000",
  "amount": 500,
  "tx_ref": "etg-550e8400-e29b-41d4-a716-446655440001"  // optional
}
```

**Returned by Ethiogram:**
```json
{
  "status": "ok",
  "new_balance": 12500
}
```

**Verification:**
- ✅ Matches documented contract in docstring
- ✅ Error cases return HTTP error codes (400/401), not "ok" responses
- ✅ No ambiguous error responses

---

## 6. ROUTER REGISTRATION

### Located in [app/main.py](app/main.py)

**Lines 74–88:**
```python
from app.api import auth, bots, webhooks, admin, billing, agents, dashboard, ...
from app.api.internal import router as internal_router

app.include_router(billing.router, prefix=settings.api_prefix)            # /api/v1/billing/*
app.include_router(internal_router, prefix=settings.api_prefix)           # /api/v1/internal/*
```

**Endpoints:**
- `POST /api/v1/billing/webhook/chapa` — public webhook (unauth)
- `POST /api/v1/internal/chapa-credit` — service-to-service (header-protected)

**Status:**
- ✅ Both route prefixes correct
- ✅ Webhook excluded from OpenAPI schema (`include_in_schema=False`)
- ✅ S2S endpoint also excluded from schema

---

## 7. SUMMARY OF ISSUES

| # | Severity | Issue | Location | Impact | Fix |
|---|----------|-------|----------|--------|-----|
| 1 | Medium | `chapa_webhook_secret` configured but unused | config.py:109 | No webhook signature validation; must rely on Chapa re-verify | Add HMAC-SHA256 validation in webhook handler |
| 2 | **HIGH** | Invalid FastAPI parameter binding for webhook body | billing.py:298 | `request_data: dict` without `Body()` will not receive JSON body; webhook likely fails silently | Add `Body(...)` annotation or use `Request` object |
| 3 | Medium | No webhook signature validation | billing.py:296–351 | Unprotected webhook endpoint; any actor can POST to credit wallets | Implement HMAC validation before crediting |
| 4 | Low | No request body signature on S2S endpoint | internal.py:24–72 | Header-only protection; compromised service can forge requests | Acceptable in VPC-isolated GCP environment; document assumption |

---

## 8. SECONDARY OBSERVATIONS

### ✅ Error Handling — Graceful Degradation

**Chapa Service:**
- Returns `None` on any error (safe fallback)
- Logs all failures with context
- Never crashes the recharge flow

**Webhook:**
- Always returns 200 (Chapa expects this)
- Idempotent on duplicate tx_refs
- Logs warnings/errors without propagating

**S2S Endpoint:**
- Returns explicit error codes (400/401)
- Validates all inputs before crediting
- Clear error messages

### ⚠️ Testing Gaps

No dedicated tests found for:
- Chapa webhook signature validation (missing feature)
- Webhook idempotency (duplicate tx_ref)
- S2S credential validation
- Amount mismatch handling

---

## 9. RECOMMENDATIONS

### Immediate (Before Production)

1. **Fix webhook parameter binding** (ISSUE #2)
   - Change `request_data: dict` to `request_data: dict = Body(...)`
   - Or refactor to use `Request` object directly for raw body access

2. **Add webhook signature validation** (ISSUE #3)
   - Implement HMAC-SHA256 verification using `settings.chapa_webhook_secret`
   - Check `X-Chapa-Signature` header (or equivalent per Chapa API docs)
   - Fail fast (401) on invalid signatures

3. **Document signature header name**
   - Confirm exact header name from Chapa API documentation
   - Ensure callback URL matches registered URL in Chapa dashboard

### Short-term

4. **Add test coverage:**
   ```python
   # tests/test_chapa_webhook.py
   - test_invalid_webhook_signature → 401
   - test_duplicate_tx_ref → idempotent credit
   - test_amount_mismatch → no credit
   - test_missing_business_id → no crash
   ```

5. **Add structured logging:**
   - Log webhook signature validation result (pass/fail)
   - Log S2S authentication attempts (pass/fail)
   - Track crediting latency (webhook + re-verify time)

6. **Monitoring & Alerts:**
   - Alert on webhook signature mismatches (potential attack)
   - Alert on repeated amount mismatches (data corruption)
   - Dashboard metric for webhook latency (Chapa re-verify time)

---

## 10. ODAFLUX FORWARDING CONTRACT

**Odaflux calls Ethiogram when:**
1. Chapa payment confirms on Odaflux's side
2. User completes payment flow and Chapa notifies Odaflux webhook
3. Odaflux calls `/api/v1/internal/chapa-credit` to credit Ethiogram business

**Payload Flow:**
```
Chapa API
  ↓
Odaflux webhook ← receives Chapa notification
  ↓
Odaflux validates Chapa signature
  ↓
Odaflux calls Ethiogram S2S endpoint (with X-Internal-Key header)
  ↓
Ethiogram validates header
  ↓
Ethiogram credits business wallet & logs transaction
```

**Contract Compliance:** ✅ **GOOD**
- Ethiogram endpoint matches documented interface
- Error handling is explicit (400/401)
- Response includes new_balance for verification
- tx_ref tracked for audit trail

---

## 11. DEPLOYMENT CHECKLIST

Before deploying Chapa integration to production:

- [ ] Set `CHAPA_SECRET_KEY` (or `CHAPA_PAYMENT_TOKEN`) in Cloud Run secret
- [ ] Set `CHAPA_INTERNAL_KEY` in Cloud Run secret (shared with Odaflux operator)
- [ ] Set `CHAPA_WEBHOOK_SECRET` in Cloud Run secret
- [ ] **FIX ISSUE #2:** Correct webhook parameter binding
- [ ] **FIX ISSUE #3:** Add webhook signature validation
- [ ] Register webhook URL in Chapa dashboard: `https://api.ethiogram.com/api/v1/billing/webhook/chapa`
- [ ] Test end-to-end: dummy recharge → Chapa sandbox → webhook → credit confirmed
- [ ] Verify Odaflux can reach S2S endpoint with correct header
- [ ] Enable structured logging on webhook & S2S endpoints
- [ ] Set up alerts for webhook signature failures

