# app/services/anthropic_service.py
from typing import Optional

from app.core.config import settings
from app.core.exceptions import ExternalServiceError, ModelUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Claude model IDs supported as premium option
_CLAUDE_MODELS = {
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-haiku-4-5-20251001",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
}


class AnthropicService:
    """
    Claude via the Anthropic SDK.
    Premium AI option — routed here when model_id starts with "claude".
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not settings.anthropic_api_key:
                raise ExternalServiceError("Anthropic", "ANTHROPIC_API_KEY not configured")
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            except ImportError:
                raise ExternalServiceError("Anthropic", "anthropic SDK not installed")
        return self._client

    async def complete(
        self,
        model_id: str,
        messages: list[dict],
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> tuple[str, dict]:
        """
        Call Claude and return (response_text, token_counts).
        Anthropic uses a separate top-level system param, not a system message.
        """
        client = self._get_client()

        # Anthropic requires alternating user/assistant turns;
        # deduplicate consecutive same-role messages by merging content.
        clean_messages = _normalise_messages(messages)

        kwargs: dict = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": clean_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            response = await client.messages.create(**kwargs)
            text = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )
            tokens = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            logger.info(
                "Anthropic completion",
                model_id=model_id,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
            )
            return text, tokens

        except Exception as exc:
            logger.error("Anthropic completion failed", model_id=model_id, error=str(exc))
            raise ModelUnavailableError(model_id)

    async def complete_with_cache(
        self,
        model_id: str,
        messages: list[dict],
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        cache_system: bool = True,
    ) -> tuple[str, dict]:
        """
        Variant with prompt caching enabled on the system prompt.
        Reduces costs significantly for repeated system prompts (e.g. Father Agent prompts).
        """
        client = self._get_client()
        clean_messages = _normalise_messages(messages)

        system_content = None
        if system_prompt and cache_system:
            system_content = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif system_prompt:
            system_content = system_prompt

        kwargs: dict = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": clean_messages,
            "temperature": temperature,
        }
        if system_content:
            kwargs["system"] = system_content

        try:
            response = await client.messages.create(**kwargs)
            text = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )
            usage = response.usage
            tokens = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0),
                "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            }
            logger.info(
                "Anthropic cached completion",
                model_id=model_id,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
                cache_read=tokens["cache_read_tokens"],
                cache_write=tokens["cache_write_tokens"],
            )
            return text, tokens

        except Exception as exc:
            logger.error("Anthropic cached completion failed", model_id=model_id, error=str(exc))
            raise ModelUnavailableError(model_id)


def _normalise_messages(messages: list[dict]) -> list[dict]:
    """
    Anthropic requires strictly alternating user/assistant turns.
    Merge consecutive same-role messages into one.
    """
    if not messages:
        return []
    normalised: list[dict] = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        if normalised and normalised[-1]["role"] == role:
            normalised[-1]["content"] += f"\n{content}"
        else:
            normalised.append({"role": role, "content": content})
    return normalised


anthropic_service = AnthropicService()
