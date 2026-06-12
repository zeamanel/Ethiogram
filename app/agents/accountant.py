# app/agents/accountant.py
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResponse, BaseAgent
from app.core.logging import get_logger
from app.db.models import BusinessBrainConfig
from app.services.ocr_service import ocr_service
from app.services.telegram_service import MessageEnvelope

logger = get_logger(__name__)


class AccountantAgent(BaseAgent):
    """
    Specialist agent for receipt processing, expense tracking, and financial Q&A.

    Extended behaviour over BaseAgent:
    - Detects incoming photo/document messages and runs OCR before the LLM call
    - Extracts structured receipt data (vendor, date, total, line items)
    - Enriches the user message with OCR'd text so the model can analyse the receipt
    - Returns structured JSON summary alongside the conversational reply
    """

    agent_name = "Accountant"

    async def pre_process(self, envelope: MessageEnvelope, db: AsyncSession) -> str:
        """
        If the message contains an image or document, download and OCR it first.
        Prepends the extracted text to the user's caption (if any).
        """
        if envelope.media_type not in ("photo", "document") or not envelope.media_file_id:
            return envelope.text or ""

        # We need the raw token to download from Telegram.
        # The token is stored on the envelope's raw payload bot context;
        # for now we retrieve it from the raw update metadata.
        raw_token = envelope.raw.get("_bot_token")
        if not raw_token:
            logger.warning(
                "AccountantAgent: no bot token on envelope, skipping OCR",
                bot_id=envelope.bot_id,
            )
            return envelope.text or ""

        try:
            from app.services.telegram_service import telegram_service
            download_url = await telegram_service.get_file_download_url(
                raw_token, envelope.media_file_id
            )

            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(download_url)
                file_bytes = resp.content

            filename = f"upload.{envelope.media_type if envelope.media_type != 'photo' else 'jpg'}"
            extracted_text = await ocr_service.extract_text(file_bytes, filename)

            if not extracted_text.strip():
                return envelope.text or "I sent you an image."

            # Combine OCR output with any caption the user typed
            caption = envelope.text or ""
            combined = f"[Extracted from image/document]\n{extracted_text}"
            if caption:
                combined = f"{caption}\n\n{combined}"

            logger.info(
                "OCR complete",
                agent=self.agent_name,
                chars=len(extracted_text),
                business_id=envelope.business_id,
            )
            return combined

        except Exception as exc:
            logger.error(
                "AccountantAgent OCR failed",
                error=str(exc),
                business_id=envelope.business_id,
            )
            return envelope.text or "I received your image but couldn't read it clearly."

    def build_system_prompt(
        self,
        brain_config: Optional[BusinessBrainConfig],
        child_data: Optional[dict],
        chunks: list[dict],
    ) -> str:
        base = super().build_system_prompt(brain_config, child_data, chunks)

        accounting_addendum = (
            "\n\nYou are also an expert accountant and financial analyst. "
            "When given receipt or invoice text:\n"
            "1. Summarise the key details: vendor, date, total amount, line items.\n"
            "2. Flag any unusual items or discrepancies.\n"
            "3. Suggest how to categorise the expense.\n"
            "4. Be precise with numbers — double-check any arithmetic.\n"
            "5. Format currency values clearly (include 'ETB' or 'USD' when identifiable)."
        )
        return base + accounting_addendum

    async def extract_and_summarise_receipt(
        self, text: str
    ) -> dict:
        """
        Extract structured data from OCR'd receipt text.
        Returns a dict with vendor, date, total, items, and categories.
        """
        structured = ocr_service.extract_receipt_data(text)

        # Attempt LLM-powered line item extraction via a lightweight prompt
        try:
            from app.services.model_router import model_router
            prompt = (
                "Extract ALL line items from this receipt text as JSON. "
                "Return only valid JSON in this format: "
                '{"items": [{"name": str, "qty": int, "unit_price": float, "total": float}], '
                '"subtotal": float, "tax": float, "total": float, "currency": str}. '
                "If a field is unclear, use null.\n\nReceipt text:\n" + text[:2000]
            )
            response_text, _, _ = await model_router.execute_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a precise JSON extractor. Return only valid JSON, no markdown.",
                max_tokens=512,
                temperature=0.0,
            )
            # Strip potential markdown fences
            clean = response_text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            llm_data = json.loads(clean)
            structured.update(llm_data)
        except Exception as exc:
            logger.warning("LLM receipt extraction failed, using regex only", error=str(exc))

        return structured


accountant_agent = AccountantAgent()
