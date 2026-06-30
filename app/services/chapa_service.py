# app/services/chapa_service.py
"""Chapa payment integration — hosted checkout via the REST API.

Two calls:
  • initialize() creates a transaction and returns the hosted checkout URL the
    owner is sent to.
  • verify() confirms a transaction server-to-server. The webhook ALWAYS calls
    verify() before crediting, so a spoofed callback can never top up a wallet.

Configured by CHAPA_SECRET_KEY (CHASECK_…); a missing key makes both calls a
safe no-op (returns None) so the recharge endpoint just yields no payment URL.
"""
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    return (settings.chapa_base_url or "https://api.chapa.co/v1").rstrip("/")


class ChapaService:
    @property
    def configured(self) -> bool:
        return bool(settings.chapa_secret_key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.chapa_secret_key}",
            "Content-Type": "application/json",
        }

    async def initialize(
        self, *, amount, currency, tx_ref: str, email: Optional[str],
        first_name: Optional[str], callback_url: str, return_url: Optional[str] = None,
    ) -> Optional[str]:
        """Create a Chapa transaction; return its hosted checkout URL (or None)."""
        if not self.configured:
            logger.warning("Chapa not configured — no CHAPA_SECRET_KEY")
            return None
        payload = {
            "amount": str(amount),
            "currency": currency or "ETB",
            "email": email or "customer@ethiogram.com",
            "first_name": (first_name or "Customer")[:50],
            "tx_ref": tx_ref,
            "callback_url": callback_url,
        }
        if return_url:
            payload["return_url"] = return_url
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{_base()}/transaction/initialize", json=payload, headers=self._headers())
            data = resp.json()
        except Exception as exc:
            logger.error("Chapa initialize call failed", error=f"{type(exc).__name__}: {exc}")
            return None
        if resp.status_code == 200 and str(data.get("status")).lower() == "success":
            return (data.get("data") or {}).get("checkout_url")
        logger.error("Chapa initialize rejected", status_code=resp.status_code, body=data)
        return None

    async def verify(self, tx_ref: str) -> Optional[dict]:
        """Verify a transaction with Chapa. Returns the data dict only when the
        payment is confirmed successful; None otherwise (incl. pending/failed)."""
        if not self.configured or not tx_ref:
            return None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{_base()}/transaction/verify/{tx_ref}", headers=self._headers())
            data = resp.json()
        except Exception as exc:
            logger.error("Chapa verify call failed", error=f"{type(exc).__name__}: {exc}")
            return None
        if resp.status_code == 200 and str(data.get("status")).lower() == "success":
            inner = data.get("data") or {}
            if str(inner.get("status")).lower() == "success":
                return inner
        return None


chapa_service = ChapaService()
