# app/services/model_router.py
import json
from typing import Optional
from uuid import UUID

from app.core.config import settings
from app.core.exceptions import AllModelsFailedError, ModelUnavailableError
from app.core.logging import get_logger
from app.db.session import get_redis

logger = get_logger(__name__)

# Redis key schema
_KEY_GLOBAL_OVERRIDE = "admin:model:global_override"
_KEY_BUSINESS_OVERRIDE = "admin:model:business:{business_id}"
_KEY_DISABLED = "admin:model:disabled:{model_id}"
_KEY_HEALTH = "admin:model:health:{model_id}"
_KEY_FAILOVER_CHAIN = "admin:model:failover_chain"
_KEY_FAIL_COUNT = "admin:model:fail_count:{model_id}"

_FAIL_THRESHOLD = 3


class ModelRouter:
    """
    Resolves which AI model to use for a given request.

    Priority (highest to lowest):
      1. Admin global override
      2. Admin per-business override
      3. Agent's assigned model
      4. Business preferred model
      5. Platform default
      6. Fallback model
      7. Emergency model
    """

    async def resolve(
        self,
        business_id: Optional[str | UUID] = None,
        agent_model_id: Optional[str] = None,
        business_preferred_model_id: Optional[str] = None,
    ) -> str:
        redis = await get_redis()
        bid = str(business_id) if business_id else None

        # 1. Global admin override
        global_override = await redis.get(_KEY_GLOBAL_OVERRIDE)
        if global_override and await self._is_available(global_override, redis):
            return global_override

        # 2. Per-business admin override
        if bid:
            biz_override = await redis.get(_KEY_BUSINESS_OVERRIDE.format(business_id=bid))
            if biz_override and await self._is_available(biz_override, redis):
                return biz_override

        # 3–5. Agent model → business preferred → platform default
        for candidate in filter(None, [
            agent_model_id,
            business_preferred_model_id,
            settings.default_model_id,
        ]):
            if await self._is_available(candidate, redis):
                return candidate

        # 6. Fallback
        if await self._is_available(settings.fallback_model_id, redis):
            return settings.fallback_model_id

        # 7. Emergency — always considered available
        return settings.emergency_model_id

    async def execute_with_fallback(
        self,
        messages: list[dict],
        system_prompt: str = "",
        business_id: Optional[str | UUID] = None,
        agent_model_id: Optional[str] = None,
        business_preferred_model_id: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> tuple[str, dict, str]:
        """
        Resolve model, call provider, auto-failover on error.
        Returns (response_text, token_counts, model_id_used).
        """
        redis = await get_redis()
        chain = await self._build_failover_chain(
            business_id, agent_model_id, business_preferred_model_id, redis
        )
        logger.info(
            "Failover chain resolved",
            chain=chain,
            business_id=str(business_id) if business_id else None,
        )

        last_error: Exception | None = None
        for model_id in chain:
            try:
                logger.info("Attempting model", model_id=model_id)
                text, tokens = await self._call_provider(
                    model_id, messages, system_prompt, max_tokens, temperature
                )
                await self._record_success(model_id, redis)
                logger.info(
                    "Model call succeeded",
                    model_id=model_id,
                    input_tokens=tokens.get("input_tokens", 0),
                    output_tokens=tokens.get("output_tokens", 0),
                    response_chars=len(text or ""),
                )
                return text, tokens, model_id
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"Model {model_id} failed, trying next in chain",
                    model_id=model_id,
                    error=f"{type(exc).__name__}: {exc}",
                    business_id=str(business_id) if business_id else None,
                )
                await self._record_failure(model_id, redis)

        logger.error(
            "All models in chain failed",
            chain=chain,
            last_error=f"{type(last_error).__name__}: {last_error}" if last_error else None,
            business_id=str(business_id) if business_id else None,
        )
        raise AllModelsFailedError() from last_error

    # ------------------------------------------------------------------
    # Admin control methods
    # ------------------------------------------------------------------

    async def set_global_override(self, model_id: str) -> None:
        redis = await get_redis()
        await redis.set(_KEY_GLOBAL_OVERRIDE, model_id)
        logger.admin_action("system", "set_global_override", "model", model_id)

    async def clear_global_override(self) -> None:
        redis = await get_redis()
        await redis.delete(_KEY_GLOBAL_OVERRIDE)
        logger.info("Global model override cleared")

    async def set_business_override(self, business_id: str, model_id: str) -> None:
        redis = await get_redis()
        await redis.set(_KEY_BUSINESS_OVERRIDE.format(business_id=business_id), model_id)

    async def clear_business_override(self, business_id: str) -> None:
        redis = await get_redis()
        await redis.delete(_KEY_BUSINESS_OVERRIDE.format(business_id=business_id))

    async def disable_model(self, model_id: str) -> None:
        redis = await get_redis()
        await redis.set(_KEY_DISABLED.format(model_id=model_id), "1")
        await redis.set(_KEY_HEALTH.format(model_id=model_id), "down")
        logger.warning(f"Model disabled by admin", model_id=model_id)

    async def enable_model(self, model_id: str) -> None:
        redis = await get_redis()
        await redis.delete(_KEY_DISABLED.format(model_id=model_id))
        await redis.set(_KEY_HEALTH.format(model_id=model_id), "healthy")
        await redis.delete(_KEY_FAIL_COUNT.format(model_id=model_id))

    async def set_failover_chain(self, chain: list[str]) -> None:
        redis = await get_redis()
        await redis.set(_KEY_FAILOVER_CHAIN, json.dumps(chain))

    async def get_model_statuses(self) -> dict[str, str]:
        redis = await get_redis()
        models = [
            settings.default_model_id,
            settings.fallback_model_id,
            settings.emergency_model_id,
        ]
        result: dict[str, str] = {}
        for model_id in models:
            disabled = await redis.get(_KEY_DISABLED.format(model_id=model_id))
            health = await redis.get(_KEY_HEALTH.format(model_id=model_id)) or "healthy"
            result[model_id] = "disabled" if disabled else health
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _is_available(self, model_id: str, redis) -> bool:
        disabled = await redis.get(_KEY_DISABLED.format(model_id=model_id))
        if disabled:
            return False
        health = await redis.get(_KEY_HEALTH.format(model_id=model_id))
        return health != "down"

    async def _build_failover_chain(
        self,
        business_id,
        agent_model_id,
        business_preferred_model_id,
        redis,
    ) -> list[str]:
        primary = await self.resolve(business_id, agent_model_id, business_preferred_model_id)

        custom_chain_raw = await redis.get(_KEY_FAILOVER_CHAIN)
        if custom_chain_raw:
            custom_chain: list[str] = json.loads(custom_chain_raw)
        else:
            custom_chain = [
                settings.default_model_id,
                settings.fallback_model_id,
                settings.emergency_model_id,
            ]

        chain = [primary]
        for m in custom_chain:
            if m not in chain:
                chain.append(m)
        # emergency always last
        if settings.emergency_model_id not in chain:
            chain.append(settings.emergency_model_id)
        return chain

    async def _record_failure(self, model_id: str, redis) -> None:
        key = _KEY_FAIL_COUNT.format(model_id=model_id)
        count = await redis.incr(key)
        await redis.expire(key, 300)  # reset window every 5 min
        if count >= _FAIL_THRESHOLD:
            await redis.set(_KEY_HEALTH.format(model_id=model_id), "degraded")
            logger.model_switched(model_id, settings.fallback_model_id, "consecutive_failures")

    async def _record_success(self, model_id: str, redis) -> None:
        await redis.delete(_KEY_FAIL_COUNT.format(model_id=model_id))
        current = await redis.get(_KEY_HEALTH.format(model_id=model_id))
        if current == "degraded":
            await redis.set(_KEY_HEALTH.format(model_id=model_id), "healthy")

    async def _call_provider(
        self,
        model_id: str,
        messages: list[dict],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, dict]:
        """Dispatch to the correct AI provider SDK based on model_id prefix."""
        provider = (
            "vertex" if model_id.startswith("gemini")
            else "openai" if model_id.startswith(("gpt", "o1", "o3"))
            else "anthropic" if model_id.startswith("claude")
            else "openai-compatible"
        )
        logger.info("Dispatching to provider", model_id=model_id, provider=provider)
        if model_id.startswith("gemini"):
            from app.services.vertex_ai_service import vertex_ai_service
            return await vertex_ai_service.complete(
                model_id, messages, system_prompt, max_tokens, temperature
            )
        elif model_id.startswith("gpt") or model_id.startswith("o1") or model_id.startswith("o3"):
            from app.services.openai_service import openai_service
            return await openai_service.complete(
                model_id, messages, system_prompt, max_tokens, temperature
            )
        elif model_id.startswith("claude"):
            from app.services.anthropic_service import anthropic_service
            return await anthropic_service.complete(
                model_id, messages, system_prompt, max_tokens, temperature
            )
        else:
            # Fallback: try OpenAI-compatible endpoint (covers Mistral, Llama via Together/Groq)
            from app.services.openai_service import openai_service
            return await openai_service.complete(
                model_id, messages, system_prompt, max_tokens, temperature
            )


model_router = ModelRouter()
