# app/core/security.py
import hashlib
import hmac
import json
import secrets
import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import bcrypt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import jwt

from app.core.config import settings

def hash_password(password: str) -> str:
    # bcrypt truncates at 72 bytes; pre-hash to support arbitrary length
    digest = hashlib.sha256(password.encode()).digest()
    return bcrypt.hashpw(base64.b64encode(digest), bcrypt.gensalt(rounds=12)).decode()

def verify_password(plain: str, hashed: str) -> bool:
    digest = hashlib.sha256(plain.encode()).digest()
    try:
        return bcrypt.checkpw(base64.b64encode(digest), hashed.encode())
    except ValueError:
        return False

def _derive_fernet_key(raw_key: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ethiogram_v1",
        iterations=100_000,
    )
    key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
    derived = kdf.derive(key_bytes)
    return base64.urlsafe_b64encode(derived)

_fernet = Fernet(_derive_fernet_key(settings.encryption_key))

def encrypt(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("Cannot encrypt empty value")
    encrypted = _fernet.encrypt(plaintext.encode("utf-8"))
    return encrypted.decode("utf-8")

def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        raise ValueError("Cannot decrypt empty value")
    decrypted = _fernet.decrypt(ciphertext.encode("utf-8"))
    return decrypted.decode("utf-8")

def hash_bot_token(token: str) -> str:
    return hashlib.sha256(
        f"{token}{settings.webhook_secret_salt}".encode()
    ).hexdigest()

def generate_webhook_secret() -> str:
    return secrets.token_hex(32)

def verify_telegram_webhook(secret_token: str, provided_token: str) -> bool:
    """
    Telegram sends the secret_token (set via setWebhook) verbatim in the
    X-Telegram-Bot-Api-Secret-Token header — not an HMAC of the body.
    Compare in constant time.
    """
    if not provided_token:
        return False
    return hmac.compare_digest(secret_token, provided_token)

def generate_secure_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)

def create_access_token(subject: str | UUID, role: str = "owner", extra_claims: Optional[dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(subject), "role": role, "iat": now,
        "exp": expire, "type": "access", "iss": "ethiogram",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

def create_refresh_token(subject: str | UUID) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload: dict[str, Any] = {
        "sub": str(subject), "iat": now, "exp": expire,
        "type": "refresh", "iss": "ethiogram", "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm],
                      options={"require": ["sub", "exp", "iat", "type"]})

def decode_access_token(token: str) -> dict[str, Any]:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return payload

def is_super_admin_telegram_id(telegram_id: int) -> bool:
    return telegram_id in settings.admin_telegram_ids

def verify_admin_secret_header(header_value: Optional[str]) -> bool:
    """Constant-time check of the admin shared secret.

    Compares the provided header against ``settings.admin_secret_value`` (the
    secret VALUE), not ``admin_secret_header`` (the header NAME). Fails closed
    when no secret is configured, so admin endpoints stay locked until
    ADMIN_SECRET_VALUE is set.
    """
    expected = settings.admin_secret_value
    if not header_value or not expected:
        return False
    return hmac.compare_digest(header_value, expected)


def verify_webapp_init_data(
    init_data: str, bot_token: str, max_age_seconds: int = 86400
) -> Optional[dict]:
    """
    Validate a Telegram Mini App ``initData`` string and return its parsed
    fields (with ``user`` decoded to a dict) on success, or None.

    Spec (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
      1. Drop the ``hash`` field; build data_check_string from the remaining
         fields as "key=value" lines sorted by key and joined with "\\n".
      2. secret_key = HMAC_SHA256(key="WebAppData", data=bot_token)
      3. expected_hash = HMAC_SHA256(key=secret_key, data=data_check_string)
      4. constant-time compare expected_hash == received hash.
    Also rejects data older than ``max_age_seconds`` (auth_date).
    """
    from urllib.parse import parse_qsl

    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)   # (1) exclude hash before the DCS
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()  # (2)
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()  # (3)
    if not hmac.compare_digest(expected_hash, received_hash):  # (4)
        return None

    # Freshness: auth_date must be present, numeric, and within the window.
    if max_age_seconds:
        try:
            auth_ts = int(pairs.get("auth_date", ""))
        except (TypeError, ValueError):
            return None
        if time.time() - auth_ts > max_age_seconds:
            return None

    result = dict(pairs)
    if "user" in result:
        try:
            result["user"] = json.loads(result["user"])
        except (ValueError, TypeError):
            pass
    return result

def encrypt_api_key(raw_key: str, provider: str) -> str:
    tagged = f"{provider}::{raw_key}"
    return encrypt(tagged)

def decrypt_api_key(encrypted_key: str, provider: str) -> str:
    decrypted = decrypt(encrypted_key)
    prefix = f"{provider}::"
    if not decrypted.startswith(prefix):
        raise ValueError(f"Decrypted key does not match provider: {provider}")
    return decrypted[len(prefix):]

def encrypt_agent_prompt(prompt: str, agent_id: str) -> tuple[str, str]:
    encrypted = encrypt(prompt)
    key_ref = "shared-key-v1"
    return encrypted, key_ref

def decrypt_agent_prompt(encrypted_prompt: str, key_ref: str) -> str:
    return decrypt(encrypted_prompt)

def encrypt_child_secrets(secrets_dict: dict) -> str:
    """Fernet-encrypt a ChildAgent's sensitive config (credentials/API keys).

    Stored in child_agents.child_secrets; never persisted plaintext, never
    rendered into the LLM prompt.
    """
    return encrypt(json.dumps(secrets_dict or {}))

def decrypt_child_secrets(blob: Optional[str]) -> dict:
    """Decrypt child_secrets back to a dict. Returns {} when empty."""
    if not blob:
        return {}
    return json.loads(decrypt(blob))

def generate_referral_code() -> str:
    return secrets.token_hex(4).upper()

def generate_order_id() -> str:
    return f"ETH-{secrets.token_hex(3).upper()}"

def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    if len(value) <= visible_chars * 2:
        return "•" * len(value)
    return value[:visible_chars] + "•" * (len(value) - visible_chars * 2) + value[-visible_chars:]
