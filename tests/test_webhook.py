# tests/test_webhook.py
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


class TestWebhookSignature:
    """
    Telegram echoes the secret_token (set via setWebhook) verbatim in the
    X-Telegram-Bot-Api-Secret-Token header — it is NOT an HMAC of the body.
    Verification must be a constant-time comparison of header == stored secret.
    """

    async def test_matching_secret_token_passes(self):
        from app.api.webhooks import _verify_signature
        assert _verify_signature("d5860f4e_secret_token", "d5860f4e_secret_token") is True

    async def test_mismatched_secret_token_fails(self):
        from app.api.webhooks import _verify_signature
        assert _verify_signature("correct_secret", "wrong_secret") is False

    async def test_missing_header_fails(self):
        from app.api.webhooks import _verify_signature
        assert _verify_signature("correct_secret", "") is False


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
