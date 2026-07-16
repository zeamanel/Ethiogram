"""Amharic speakers are routed to the configured Gemini models first."""
import pytest

import app.services.model_router as mr
from app.core.config import settings
from app.services.model_router import model_router


class _Redis:
    """Async redis stub. ``disabled`` is a set of model_ids reported disabled;
    everything else returns None (no overrides, healthy, no custom chain)."""
    def __init__(self, disabled=()):
        self.disabled = set(disabled)

    async def get(self, key):
        if "disabled" in key:
            for m in self.disabled:
                if m in key:
                    return "1"
        return None

    async def setex(self, *a):
        return True

    async def incr(self, *a):
        return 1

    async def expire(self, *a):
        return True

    async def delete(self, *a):
        return 1


def _patch_redis(monkeypatch, redis):
    async def _get():
        return redis
    monkeypatch.setattr(mr, "get_redis", _get)
    return redis


@pytest.mark.asyncio
async def test_amharic_prepends_gemini_chain(monkeypatch):
    r = _patch_redis(monkeypatch, _Redis())
    chain = await model_router._build_failover_chain(None, None, None, r, language="am")
    assert chain[0] == settings.amharic_primary_model_id      # Gemini 2.5 Pro first
    assert chain[1] == settings.amharic_secondary_model_id    # then Gemini 2.5 Flash
    assert chain[-1] == settings.emergency_model_id           # emergency still last


@pytest.mark.asyncio
async def test_non_amharic_uses_normal_chain(monkeypatch):
    r = _patch_redis(monkeypatch, _Redis())
    chain = await model_router._build_failover_chain(None, None, None, r, language="en")
    assert settings.amharic_primary_model_id not in chain
    assert chain[0] == settings.default_model_id              # normal primary first


@pytest.mark.asyncio
async def test_amharic_skips_disabled_gemini(monkeypatch):
    r = _patch_redis(monkeypatch, _Redis(disabled={settings.amharic_primary_model_id}))
    chain = await model_router._build_failover_chain(None, None, None, r, language="am")
    assert settings.amharic_primary_model_id not in chain         # disabled → skipped
    assert chain[0] == settings.amharic_secondary_model_id        # Flash still first


@pytest.mark.asyncio
async def test_execute_with_fallback_tries_gemini_first_for_amharic(monkeypatch):
    _patch_redis(monkeypatch, _Redis())
    tried = []

    async def _call(model_id, messages, system_prompt, max_tokens, temperature):
        tried.append(model_id)
        return "ሰላም! እንዴት ልርዳዎት?", {"input_tokens": 3, "output_tokens": 5}
    monkeypatch.setattr(model_router, "_call_provider", _call)

    text, _tokens, used = await model_router.execute_with_fallback(
        messages=[{"role": "user", "content": "ሰላም"}], language="am")
    assert tried[0] == settings.amharic_primary_model_id
    assert used == settings.amharic_primary_model_id
