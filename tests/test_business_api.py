# tests/test_business_api.py
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str) -> str:
    """Register a user and return their access token."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SecurePass123!", "full_name": "Owner"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestCreateBusiness:
    async def test_create_business(self, client: AsyncClient, db):
        token = await _register(client, "biz@example.com")
        resp = await client.post(
            "/api/v1/dashboard/businesses",
            json={"name": "Abebe Coffee", "category": "retail"},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["name"] == "Abebe Coffee"
        assert data["slug"] == "abebe-coffee"
        assert data["total_bots"] == 0
        assert data["etg_balance"] == 0

    async def test_duplicate_name_gets_unique_slug(self, client: AsyncClient, db):
        token = await _register(client, "dup-biz@example.com")
        first = await client.post(
            "/api/v1/dashboard/businesses",
            json={"name": "Sunrise Bakery"},
            headers=_auth(token),
        )
        second = await client.post(
            "/api/v1/dashboard/businesses",
            json={"name": "Sunrise Bakery"},
            headers=_auth(token),
        )
        assert first.json()["slug"] == "sunrise-bakery"
        assert second.json()["slug"] == "sunrise-bakery-2"

    async def test_blank_name_rejected(self, client: AsyncClient, db):
        token = await _register(client, "blank@example.com")
        resp = await client.post(
            "/api/v1/dashboard/businesses",
            json={"name": "   "},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    async def test_create_requires_auth(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/dashboard/businesses", json={"name": "No Auth Co"}
        )
        assert resp.status_code == 401

    async def test_created_business_appears_in_list(self, client: AsyncClient, db):
        token = await _register(client, "list-biz@example.com")
        await client.post(
            "/api/v1/dashboard/businesses",
            json={"name": "Listed Co"},
            headers=_auth(token),
        )
        resp = await client.get(
            "/api/v1/dashboard/businesses", headers=_auth(token)
        )
        assert resp.status_code == 200
        names = [b["name"] for b in resp.json()]
        assert "Listed Co" in names


class TestOnboardBot:
    async def test_onboard_bot_credits_welcome_bonus(self, client: AsyncClient, db):
        token = await _register(client, "onboard@example.com")
        biz = await client.post(
            "/api/v1/dashboard/businesses",
            json={"name": "Onboard Co"},
            headers=_auth(token),
        )
        business_id = biz.json()["id"]

        fake_tg = AsyncMock()
        fake_tg.get_me = AsyncMock(
            return_value={"username": "onboard_bot", "first_name": "Onboard Bot"}
        )
        fake_tg.set_webhook = AsyncMock(return_value=True)

        with patch("app.services.bot_onboarding.telegram_service", fake_tg):
            resp = await client.post(
                "/api/v1/bots",
                json={"token": "123456:FAKE-BOTFATHER-TOKEN", "business_id": business_id},
                headers=_auth(token),
            )

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["bot_username"] == "onboard_bot"
        assert data["status"] == "active"
        fake_tg.set_webhook.assert_awaited_once()

        # Wallet bonus reflected in the dashboard list
        listing = await client.get(
            "/api/v1/dashboard/businesses", headers=_auth(token)
        )
        created = next(b for b in listing.json() if b["id"] == business_id)
        assert created["etg_balance"] > 0
        assert created["total_bots"] == 1

    async def test_onboard_bot_for_unowned_business_404(self, client: AsyncClient, db):
        token = await _register(client, "intruder@example.com")
        import uuid

        resp = await client.post(
            "/api/v1/bots",
            json={"token": "x:y", "business_id": str(uuid.uuid4())},
            headers=_auth(token),
        )
        assert resp.status_code == 404
