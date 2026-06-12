# app/services/vertex_ai_service.py
import asyncio
from typing import Optional

from app.core.config import settings
from app.core.exceptions import ExternalServiceError, ModelUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Maps Vertex model IDs to their generative SDK names
_GEMINI_MODELS = {
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite-001",
    "gemini-1.5-pro-001",
    "gemini-1.5-flash-001",
}


class VertexAiService:
    """
    Gemini via Vertex AI SDK.
    All SDK calls are blocking; dispatched to executor for async compatibility.
    """

    def __init__(self):
        self._initialized = False

    def _ensure_init(self) -> None:
        if not self._initialized:
            try:
                import vertexai
                vertexai.init(
                    project=settings.vertex_project,
                    location=settings.vertex_ai_location,
                )
                self._initialized = True
            except Exception as exc:
                raise ExternalServiceError("Vertex AI", f"Init failed: {exc}")

    async def complete(
        self,
        model_id: str,
        messages: list[dict],
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> tuple[str, dict]:
        """
        Call Gemini and return (response_text, token_counts).
        token_counts = {"input_tokens": int, "output_tokens": int}
        """
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._complete_sync,
            model_id,
            messages,
            system_prompt,
            max_tokens,
            temperature,
        )

    def _complete_sync(
        self,
        model_id: str,
        messages: list[dict],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, dict]:
        self._ensure_init()

        try:
            from vertexai.generative_models import GenerativeModel, GenerationConfig, Content, Part
        except ImportError:
            raise ExternalServiceError("Vertex AI", "vertexai SDK not installed")

        try:
            model = GenerativeModel(
                model_name=model_id,
                system_instruction=system_prompt if system_prompt else None,
            )

            # Convert OpenAI-style message dicts to Vertex Content objects
            history: list[Content] = []
            last_user_text = ""
            for msg in messages:
                role = "user" if msg["role"] == "user" else "model"
                content_text = msg.get("content", "")
                if msg == messages[-1] and role == "user":
                    last_user_text = content_text
                else:
                    history.append(Content(role=role, parts=[Part.from_text(content_text)]))

            chat = model.start_chat(history=history)
            response = chat.send_message(
                last_user_text or (messages[-1].get("content", "") if messages else ""),
                generation_config=GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )

            text = response.text or ""
            usage = response.usage_metadata
            tokens = {
                "input_tokens": getattr(usage, "prompt_token_count", 0),
                "output_tokens": getattr(usage, "candidates_token_count", 0),
            }
            logger.info(
                "Vertex AI completion",
                model_id=model_id,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
            )
            return text, tokens

        except Exception as exc:
            logger.error("Vertex AI completion failed", model_id=model_id, error=str(exc))
            raise ModelUnavailableError(model_id)


vertex_ai_service = VertexAiService()
