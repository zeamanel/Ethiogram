# app/services/transcription_service.py
"""Voice-note transcription (Amharic-first) via Gemini.

Telegram voice notes arrive as OGG/Opus. Gemini accepts audio/ogg natively, so
no transcoding is needed. Two backends, tried in order:

  1. Gemini API (generativelanguage.googleapis.com) — used when GEMINI_API_KEY
     is configured. One HTTPS call, no SDK.
  2. Vertex AI SDK — used when the service runs on GCP with ADC (Cloud Run
     service account) and the Vertex API is enabled.

Both are fail-safe: any error returns None and the caller sends a polite
"couldn't hear that" reply instead of crashing the webhook.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_PROMPT = (
    "Transcribe this voice message exactly as spoken. It is most likely in "
    "Amharic, but may be in English or a mix. Reply with ONLY the transcript "
    "text — no translation, no commentary, no quotes. If the audio is silent "
    "or unintelligible, reply with an empty string."
)


class TranscriptionService:
    @property
    def configured(self) -> bool:
        """True when at least one backend could work. Vertex availability can't
        be known without calling it, so this only guarantees the API-key path."""
        return bool(settings.gemini_api_key)

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> Optional[str]:
        """Return the transcript, or None when transcription isn't possible."""
        if not audio_bytes:
            return None
        text = await self._via_gemini_api(audio_bytes, mime_type)
        if text is None:
            text = await self._via_vertex(audio_bytes, mime_type)
        if text is not None:
            text = text.strip()
        return text or None

    # ── backend 1: Gemini API key ────────────────────────────────────────────
    async def _via_gemini_api(self, audio_bytes: bytes, mime_type: str) -> Optional[str]:
        if not settings.gemini_api_key:
            return None
        model = settings.transcription_model_id
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": _PROMPT},
                    {"inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(audio_bytes).decode("ascii"),
                    }},
                ],
            }],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.0},
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url, json=payload,
                    headers={"x-goog-api-key": settings.gemini_api_key})
            if resp.status_code != 200:
                logger.warning("Gemini transcription rejected",
                               status_code=resp.status_code, body=resp.text[:300])
                return None
            data = resp.json()
            parts = (((data.get("candidates") or [{}])[0].get("content") or {})
                     .get("parts") or [])
            return "".join(p.get("text", "") for p in parts)
        except Exception as exc:
            logger.warning("Gemini transcription call failed",
                           error=f"{type(exc).__name__}: {exc}")
            return None

    # ── backend 2: Vertex AI (ADC on Cloud Run) ──────────────────────────────
    async def _via_vertex(self, audio_bytes: bytes, mime_type: str) -> Optional[str]:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._vertex_sync, audio_bytes, mime_type)
        except Exception as exc:
            logger.warning("Vertex transcription failed",
                           error=f"{type(exc).__name__}: {exc}")
            return None

    def _vertex_sync(self, audio_bytes: bytes, mime_type: str) -> Optional[str]:
        import vertexai
        from vertexai.generative_models import GenerativeModel, Part
        vertexai.init(project=settings.vertex_project,
                      location=settings.vertex_ai_location)
        model = GenerativeModel(settings.transcription_model_id)
        response = model.generate_content(
            [_PROMPT, Part.from_data(data=audio_bytes, mime_type=mime_type)])
        return response.text or None


transcription_service = TranscriptionService()
