# app/core/security.py
import hashlib
import hmac
import secrets
import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return _pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)

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

def verify_telegram_webhook(request_body: bytes, secret_token: str, provided_hash: str) -> bool:
    expected = hmac.new(secret_token.encode(), request_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided_hash)

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
    if not header_value:
        return False
    return hmac.compare_digest(header_value, settings.admin_secret_header)

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

def generate_referral_code() -> str:
    return secrets.token_hex(4).upper()

def generate_order_id() -> str:
    return f"ETH-{secrets.token_hex(3).upper()}"

def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    if len(value) <= visible_chars * 2:
        return "•" * len(value)
    return value[:visible_chars] + "•" * (len(value) - visible_chars * 2) + value[-visible_chars:]
