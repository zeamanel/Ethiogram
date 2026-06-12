# tests/test_webhook.py
import hashlib
import hmac
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.asyncio

SAMPLE_UPDATE = {
    "update_id": 123456789,
    "message": {
        "message_id": 1,
        "from": {
            "id": 111222333,
            "is_bot": False,
            "first_name": "Alem",
            "username": "alem_test",
            "language_code": "en",
        },
        "chat": {"id": 111222333, "first_name": "Alem", "type": "private"},
        "date": 1700000000,
        "text": "Hello",
    },
}


def _make_telegram_signature(body: bytes, secret: str) -> str:
    """Compute Telegram webhook HMAC-SHA256 signature."""
    secret_key = hmac.new(b"WebAppData", secret.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret_key, body, hashlib.sha256).hexdigest()
    return sig


class TestWebhookUnknownHash:
    async def test_unknown_token_hash_returns_ok_silently(self, client):
        """Security: unknown token hash must not leak info — always 200."""
        response = await client.post(
            "/webhook/nonexistent-hash",
            content=json.dumps(SAMPLE_UPDATE).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}


class TestWebhookMalformedBody:
    async def test_invalid_json_returns_ok(self, client):
        """Malformed JSON should not crash the server."""
        response = await client.post(
            "/webhook/any-hash",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
