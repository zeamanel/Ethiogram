# app/services/chapa_service.py
"""Chapa payment integration — hosted checkout via the Lulit gateway.

The recharge flow now delegates Chapa initialization to Lulit, which returns a
checkout URL for the hosted payment flow.
"""
import os
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


LULIT_BASE_URL = os.getenv("LULIT_BASE_URL", "https://musa-ai-backend-760742977917.us-central1.run.app")


def _base() -> str:
    return (settings.chapa_base_url or "https://api.chapa.co/v1").rstrip("/")


class ChapaService:
    @property
    def configured(self) -> bool:
        return bool(settings.chapa_secret_key or LULIT_BASE_URL)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
        }

    async def initialize(
        self, *, amount, currency, tx_ref: str, email: Optional[str],
        first_name: Optional[str], callback_url: str, return_url: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> Optional[str]:
        """Create a payment session through Lulit and return the hosted checkout URL."""
        if not self.configured:
            logger.warning("Chapa not configured — no CHAPA_SECRET_KEY")
            return None
        payload = {
            "amount_etb": int(amount),
            "callback_url": callback_url,
            "tx_ref": tx_ref,
        }
        if email:
            payload["email"] = email
        if first_name:
            payload["first_name"] = first_name[:50]
        if return_url:
            payload["return_url"] = return_url
        if meta:
            payload["meta"] = meta
        logger.info("Calling Lulit initiate", payload=payload)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{LULIT_BASE_URL}/api/v1/chapa/initiate",
                    json=payload,
                    headers=self._headers(),
                )
            data = resp.json()
            body = getattr(resp, "text", None)
            if body is None and hasattr(resp, "content"):
                body = resp.content
            logger.info("Lulit response", status=resp.status_code, body=body)
        except Exception as exc:
            logger.error("Lulit initialize call failed", error=f"{type(exc).__name__}: {exc}")
            return None
        if resp.status_code == 200:
            return data.get("checkout_url")
        logger.error("Lulit initialize rejected", status_code=resp.status_code, body=data)
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
