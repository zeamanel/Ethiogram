# tests/test_security.py
import pytest
import jwt

from app.core.security import (
    encrypt,
    decrypt,
    hash_bot_token,
    create_access_token,
    create_refresh_token,
    verify_access_token,
    encrypt_api_key,
    decrypt_api_key,
    hash_password,
    verify_password,
)
from app.core.config import settings


class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "my-secret-bot-token"
        ciphertext = encrypt(plaintext)
        assert ciphertext != plaintext
        assert decrypt(ciphertext) == plaintext

    def test_different_plaintexts_produce_different_ciphertexts(self):
        a = encrypt("token-a")
        b = encrypt("token-b")
        assert a != b

    def test_encrypt_produces_string(self):
        result = encrypt("test")
        assert isinstance(result, str)

    def test_decrypt_wrong_ciphertext_raises(self):
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

    def test_provider_prefix_included(self):
        enc = encrypt_api_key("sk-test", "openai")
        assert enc.startswith("openai:")

    def test_wrong_provider_raises(self):
        enc = encrypt_api_key("sk-test", "openai")
        with pytest.raises(ValueError):
            decrypt_api_key(enc, "anthropic")


class TestJWT:
    def test_create_and_verify_access_token(self):
        import uuid
        user_id = str(uuid.uuid4())
        token = create_access_token({"sub": user_id, "role": "user"})
        payload = verify_access_token(token)
        assert payload["sub"] == user_id

    def test_expired_token_raises(self):
        from datetime import timedelta
        token = create_access_token({"sub": "test"}, expires_delta=timedelta(seconds=-1))
        with pytest.raises(jwt.ExpiredSignatureError):
            verify_access_token(token)

    def test_invalid_token_raises(self):
        with pytest.raises(jwt.InvalidTokenError):
            verify_access_token("not.a.valid.token")

    def test_refresh_token_is_different_from_access(self):
        payload = {"sub": "user-id"}
        access = create_access_token(payload)
        refresh = create_refresh_token(payload)
        assert access != refresh


class TestPasswordHashing:
    def test_hash_and_verify(self):
        password = "MyStr0ngP@ssword!"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_hash_is_not_plaintext(self):
        password = "secret"
        assert hash_password(password) != password
