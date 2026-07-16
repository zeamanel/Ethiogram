# tests/test_security.py
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_token,
    decrypt,
    decrypt_api_key,
    encrypt,
    encrypt_api_key,
    encrypt_agent_prompt,
    decrypt_agent_prompt,
    decrypt_child_secrets,
    encrypt_child_secrets,
    generate_order_id,
    generate_referral_code,
    hash_bot_token,
    hash_password,
    mask_sensitive,
    verify_admin_secret_header,
    verify_password,
)


class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "my-secret-bot-token"
        ciphertext = encrypt(plaintext)
        assert ciphertext != plaintext
        assert decrypt(ciphertext) == plaintext

    def test_different_plaintexts_produce_different_ciphertexts(self):
        assert encrypt("token-a") != encrypt("token-b")

    def test_encrypt_produces_string(self):
        assert isinstance(encrypt("test"), str)

    def test_decrypt_garbage_raises(self):
        with pytest.raises(Exception):
            decrypt("not-valid-ciphertext")


class TestBotTokenHash:
    def test_hash_is_deterministic(self):
        token = "1234567890:ABCDEFGhijklmnopqrstuvwxyz"
        assert hash_bot_token(token) == hash_bot_token(token)

    def test_hash_different_tokens_differ(self):
        assert hash_bot_token("token-a") != hash_bot_token("token-b")

    def test_hash_is_hex_string(self):
        result = hash_bot_token("mytoken")
        assert isinstance(result, str)
        int(result, 16)  # must be valid hex


class TestApiKeyEncryption:
    def test_openai_roundtrip(self):
        key = "sk-test1234"
        enc = encrypt_api_key(key, "openai")
        assert decrypt_api_key(enc, "openai") == key

    def test_ciphertext_does_not_leak_provider_or_key(self):
        enc = encrypt_api_key("sk-test", "openai")
        assert "openai" not in enc
        assert "sk-test" not in enc

    def test_wrong_provider_raises(self):
        enc = encrypt_api_key("sk-test", "openai")
        with pytest.raises(ValueError):
            decrypt_api_key(enc, "anthropic")


class TestAgentPromptEncryption:
    def test_roundtrip(self):
        prompt = "You are a helpful accountant for Ethiopian SMBs."
        encrypted, key_ref = encrypt_agent_prompt(prompt, "agent-123")
        assert encrypted != prompt
        assert decrypt_agent_prompt(encrypted, key_ref) == prompt


class TestJWT:
    def test_create_and_decode_access_token(self):
        user_id = uuid.uuid4()
        token = create_access_token(user_id, role="owner")
        payload = decode_access_token(token)
        assert payload["sub"] == str(user_id)
        assert payload["role"] == "owner"
        assert payload["type"] == "access"

    def test_refresh_token_rejected_as_access(self):
        token = create_refresh_token(uuid.uuid4())
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(token)

    def test_expired_token_raises(self):
        now = datetime.now(timezone.utc)
        expired = jwt.encode(
            {
                "sub": "test", "role": "owner", "type": "access",
                "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1),
                "iss": "ethiogram",
            },
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(expired)

    def test_invalid_token_raises(self):
        with pytest.raises(jwt.InvalidTokenError):
            decode_token("not.a.valid.token")

    def test_tampered_signature_raises(self):
        token = create_access_token(uuid.uuid4())
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(token[:-4] + "AAAA")

    def test_extra_claims_included(self):
        token = create_access_token(uuid.uuid4(), extra_claims={"business_id": "biz-1"})
        payload = decode_access_token(token)
        assert payload["business_id"] == "biz-1"


class TestPasswordHashing:
    def test_hash_and_verify(self):
        password = "MyStr0ngP@ssword!"
        assert verify_password(password, hash_password(password)) is True

    def test_wrong_password_fails(self):
        assert verify_password("wrong", hash_password("correct")) is False

    def test_hash_is_not_plaintext(self):
        assert hash_password("secret") != "secret"


class TestGenerators:
    def test_referral_code_format(self):
        code = generate_referral_code()
        assert len(code) == 8
        assert code == code.upper()

    def test_order_id_unique(self):
        assert generate_order_id() != generate_order_id()

    def test_mask_sensitive(self):
        masked = mask_sensitive("1234567890:ABCDEF", visible_chars=4)
        assert masked.endswith("CDEF")
        assert "1234567890" not in masked


class TestChildSecrets:
    def test_roundtrip(self):
        data = {"calendar_id": "cal@x.com", "credentials_json": '{"token":"abc"}'}
        blob = encrypt_child_secrets(data)
        assert decrypt_child_secrets(blob) == data

    def test_ciphertext_does_not_leak_values(self):
        blob = encrypt_child_secrets({"api_key": "sk-SENSITIVE-123"})
        assert "sk-SENSITIVE-123" not in blob
        assert "api_key" not in blob

    def test_empty_and_none(self):
        assert decrypt_child_secrets(None) == {}
        assert decrypt_child_secrets("") == {}
        assert decrypt_child_secrets(encrypt_child_secrets({})) == {}


class TestAdminSecretHeader:
    """verify_admin_secret_header must compare against the secret VALUE
    (admin_secret_value), not the header NAME (admin_secret_header), and
    fail closed when no secret is configured."""

    def test_unset_secret_fails_closed(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_secret_value", None)
        # The header NAME used to pass under the old bug — it must not now.
        assert verify_admin_secret_header(settings.admin_secret_header) is False
        assert verify_admin_secret_header("anything") is False

    def test_correct_value_passes(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_secret_value", "long-random-secret")
        assert verify_admin_secret_header("long-random-secret") is True

    def test_header_name_is_rejected(self, monkeypatch):
        # Regression: sending the public header name must never authenticate.
        monkeypatch.setattr(settings, "admin_secret_value", "long-random-secret")
        assert verify_admin_secret_header(settings.admin_secret_header) is False

    def test_wrong_and_empty_values_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_secret_value", "long-random-secret")
        assert verify_admin_secret_header("wrong") is False
        assert verify_admin_secret_header("") is False
        assert verify_admin_secret_header(None) is False
