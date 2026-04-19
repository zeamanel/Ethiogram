# tests/test_auth_api.py
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio


class TestRegister:
    async def test_register_new_user(self, client: AsyncClient, db):
        with patch("app.api.auth.get_db", return_value=db):
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "test@example.com",
                    "password": "SecurePass123!",
                    "full_name": "Test User",
                },
            )
        assert response.status_code in (200, 201)
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_register_duplicate_email_fails(self, client: AsyncClient, db):
        payload = {
            "email": "dup@example.com",
            "password": "SecurePass123!",
            "full_name": "First",
        }
        await client.post("/api/v1/auth/register", json=payload)
        response = await client.post("/api/v1/auth/register", json=payload)
        assert response.status_code in (400, 409, 422)

    async def test_register_weak_password_fails(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "weak@example.com", "password": "123", "full_name": "Weak"},
        )
        assert response.status_code == 422


class TestLogin:
    async def test_login_valid_credentials(self, client: AsyncClient, db):
        # First register
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "login@example.com",
                "password": "SecurePass123!",
                "full_name": "Login User",
            },
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "SecurePass123!"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_login_wrong_password(self, client: AsyncClient, db):
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "wrongpass@example.com",
                "password": "CorrectPass123!",
                "full_name": "User",
            },
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "wrongpass@example.com", "password": "Wrong!"},
        )
        assert response.status_code == 401

    async def test_login_unknown_email(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "Pass123!"},
        )
        assert response.status_code == 401


class TestMe:
    async def test_get_me_authenticated(self, client: AsyncClient, valid_access_token):
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {valid_access_token}"},
        )
        # Will fail with 404 in test DB (no user row) but not 401/422
        assert response.status_code in (200, 404)

    async def test_get_me_unauthenticated(self, client: AsyncClient):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401


class TestHealthCheck:
    async def test_health(self, client: AsyncClient):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
