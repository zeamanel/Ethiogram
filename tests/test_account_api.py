"""Tests for GET /account/me — the end-user My Account profile."""
import pytest
from sqlalchemy import select

from app.db.models import Business, User


async def _seed_user(db, user_id):
    db.add(User(id=user_id, full_name="Selam B", username="selamb",
                language_code="am", etg_balance=42))
    await db.flush()


@pytest.mark.asyncio
async def test_account_me_returns_profile(client, db, sample_user_id, valid_access_token):
    await _seed_user(db, sample_user_id)
    db.add(Business(owner_id=sample_user_id, name="Selam Store", slug="selam-store"))
    await db.flush()

    resp = await client.get(
        "/api/v1/account/me",
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Selam B"
    assert body["username"] == "selamb"
    assert body["language_code"] == "am"
    assert body["etg_balance"] == 42
    assert body["referral_code"]                       # minted on first view
    assert body["referral_link"].endswith("start=ref_" + body["referral_code"])
    assert len(body["businesses"]) == 1
    assert body["businesses"][0]["slug"] == "selam-store"
    assert body["businesses"][0]["store_url"].endswith("/app/store/?s=selam-store")


@pytest.mark.asyncio
async def test_account_me_mints_stable_referral_code(client, db, sample_user_id, valid_access_token):
    await _seed_user(db, sample_user_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    first = (await client.get("/api/v1/account/me", headers=hdr)).json()["referral_code"]
    second = (await client.get("/api/v1/account/me", headers=hdr)).json()["referral_code"]
    assert first and first == second                    # stable across calls

    row = (await db.execute(select(User).where(User.id == sample_user_id))).scalar_one()
    assert row.referral_code == first


@pytest.mark.asyncio
async def test_account_me_unauthenticated_rejected(client):
    resp = await client.get("/api/v1/account/me")
    assert resp.status_code in (401, 403)
