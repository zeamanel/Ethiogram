"""Tests for Telegram Mini App auth: verify_webapp_init_data + POST /auth/miniapp.

Independently re-implements the Telegram signing here (in _sign) so the test
proves the verifier against a genuine signature, not its own logic.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import verify_webapp_init_data
from app.db.models import User

# Two GENUINELY different bot tokens (different bot id AND different secret).
TOKEN_A = "111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TOKEN_B = "222222:ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"


def _sign(bot_token: str, user: dict, auth_date: int | None = None, extra: dict | None = None) -> str:
    """Build a valid Telegram WebApp initData string signed with bot_token."""
    auth_date = auth_date if auth_date is not None else int(time.time())
    fields = {"auth_date": str(auth_date), "query_id": "AAH123", "user": json.dumps(user)}
    if extra:
        fields.update(extra)
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


_USER = {"id": 959519454, "first_name": "Abebe", "username": "abebe"}


def test_valid_init_data_returns_user():
    data = verify_webapp_init_data(_sign(TOKEN_A, _USER), TOKEN_A)
    assert data is not None
    assert data["user"]["id"] == 959519454
    assert data["user"]["username"] == "abebe"


def test_tampered_hash_fails():
    init = _sign(TOKEN_A, _USER)
    # flip the last hex char of the hash
    bad = init[:-1] + ("0" if init[-1] != "0" else "1")
    assert verify_webapp_init_data(bad, TOKEN_A) is None


def test_tampered_field_after_signing_fails():
    """Changing any signed field must invalidate the hash (DCS integrity)."""
    init = _sign(TOKEN_A, _USER)
    # swap the user to a different id while keeping the original hash
    forged_user = json.dumps({"id": 999, "first_name": "Mallory"})
    pairs = dict(p.split("=", 1) for p in init.split("&"))
    from urllib.parse import quote
    pairs["user"] = quote(forged_user)
    forged = "&".join(f"{k}={v}" for k, v in pairs.items())
    assert verify_webapp_init_data(forged, TOKEN_A) is None


def test_wrong_token_fails_with_genuinely_different_token():
    init = _sign(TOKEN_A, _USER)            # signed with A
    assert verify_webapp_init_data(init, TOKEN_B) is None   # verified with B (different bot + secret)
    # sanity: the same data DOES pass with the correct token
    assert verify_webapp_init_data(init, TOKEN_A) is not None


def test_expired_auth_date_fails():
    old = int(time.time()) - 90_000          # ~25h ago, > 86400 default
    assert verify_webapp_init_data(_sign(TOKEN_A, _USER, auth_date=old), TOKEN_A) is None


def test_missing_auth_date_fails():
    # sign a payload that has no auth_date at all
    fields = {"query_id": "X", "user": json.dumps(_USER)}
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN_A.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    assert verify_webapp_init_data(urlencode(fields), TOKEN_A) is None


def test_max_age_zero_skips_freshness_check():
    old = int(time.time()) - 90_000
    # with max_age_seconds=0, an old (but validly signed) payload is accepted
    assert verify_webapp_init_data(_sign(TOKEN_A, _USER, auth_date=old), TOKEN_A, max_age_seconds=0) is not None


# ── endpoint ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_miniapp_endpoint_upserts_and_returns_token(client, db, monkeypatch):
    monkeypatch.setattr(settings, "master_bot_token", TOKEN_A)
    init = _sign(TOKEN_A, {"id": 7777, "first_name": "Owner", "username": "owner1"})

    resp = await client.post("/api/v1/auth/miniapp", json={"init_data": init})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer" and body["access_token"]

    user = (await db.execute(select(User).where(User.telegram_id == 7777))).scalar_one()
    assert user.username == "owner1"
    assert str(user.id) == body["user_id"]
    assert body["is_admin"] is False           # ordinary owner is not admin


@pytest.mark.asyncio
async def test_miniapp_allowlisted_telegram_id_becomes_admin(client, db, monkeypatch):
    monkeypatch.setattr(settings, "master_bot_token", TOKEN_A)
    monkeypatch.setattr(settings, "admin_telegram_ids", [959519454])
    init = _sign(TOKEN_A, {"id": 959519454, "first_name": "Platform", "username": "owner"})

    resp = await client.post("/api/v1/auth/miniapp", json={"init_data": init})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_admin"] is True     # auto-granted via allowlist → admin dashboard

    user = (await db.execute(select(User).where(User.telegram_id == 959519454))).scalar_one()
    assert user.is_admin is True               # flag self-healed on the row


@pytest.mark.asyncio
async def test_miniapp_endpoint_rejects_bad_initdata(client, monkeypatch):
    monkeypatch.setattr(settings, "master_bot_token", TOKEN_A)
    # signed with the WRONG token -> must be rejected
    init = _sign(TOKEN_B, {"id": 8888, "first_name": "X"})
    resp = await client.post("/api/v1/auth/miniapp", json={"init_data": init})
    assert resp.status_code == 401
