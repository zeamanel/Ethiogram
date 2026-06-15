# app/services/openai_service.py
import asyncio
from typing import Optional

from app.core.config import settings
from app.core.exceptions import ExternalServiceError, ModelUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)


class OpenAiService:
    """
    OpenAI (GPT-4o, GPT-4o-mini, o1/o3) and any OpenAI-compatible endpoint.
    Used as the primary fallback when Vertex AI is unavailable.
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not settings.openai_api_key:
                raise ExternalServiceError("OpenAI", "OPENAI_API_KEY not configured")
            try:
                from openai import AsyncOpenAI
                # base_url lets us point at any OpenAI-compatible endpoint
                # (e.g. OpenRouter). When unset, the SDK defaults to OpenAI.
                self._client = AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    organization=settings.openai_org_id,
                    base_url=settings.openai_base_url or None,
                )
                logger.info(
                    "OpenAI client initialized",
                    base_url=settings.openai_base_url or "https://api.openai.com (default)",
                )
            except ImportError:
                raise ExternalServiceError("OpenAI", "openai SDK not installed")
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
        Call OpenAI chat completions and return (response_text, token_counts).
        Prepends system_prompt as a system message when provided.
        """
        client = self._get_client()

        full_messages: list[dict] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        # o1/o3 models don't support temperature or system messages
        is_reasoning = model_id.startswith(("o1", "o3"))
        kwargs: dict = {
            "model": model_id,
            "messages": full_messages if not is_reasoning else [
                m for m in full_messages if m["role"] != "system"
            ],
            "max_completion_tokens" if is_reasoning else "max_tokens": max_tokens,
        }
        if not is_reasoning:
            kwargs["temperature"] = temperature

        try:
            response = await client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content or ""
            usage = response.usage
            tokens = {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            }
            logger.info(
                "OpenAI completion",
                model_id=model_id,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
            )
            return text, tokens

        except Exception as exc:
            logger.error("OpenAI completion failed", model_id=model_id, error=str(exc))
            raise ModelUnavailableError(model_id)

    async def complete_with_tools(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> tuple[str, Optional[list[dict]], dict]:
        """
        Function-calling variant. Returns (text, tool_calls, token_counts).
        tool_calls is None when the model responded with plain text.
        """
        client = self._get_client()

        full_messages: list[dict] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            response = await client.chat.completions.create(
                model=model_id,
                messages=full_messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=max_tokens,
                temperature=temperature,
            )
            choice = response.choices[0]
            text = choice.message.content or ""
            tool_calls = None
            if choice.message.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in choice.message.tool_calls
                ]
            usage = response.usage
            tokens = {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
            }
            return text, tool_calls, tokens

        except Exception as exc:
            logger.error("OpenAI tool call failed", model_id=model_id, error=str(exc))
            raise ModelUnavailableError(model_id)


openai_service = OpenAiService()
