#!/usr/bin/env python3
"""
Quick local check that the bot's AI path works through OpenRouter.

Exercises the SAME code the webhook uses (app.services.openai_service) — i.e.
the OpenAI-compatible client with OPENAI_BASE_URL + OPENAI_API_KEY — so a
success here means the bot's reply generation will work once deployed.

No DB / Redis / Telegram required.

Usage (Git Bash, from repo root):
    export OPENAI_BASE_URL=https://openrouter.ai/api/v1
    export OPENAI_API_KEY=sk-or-YOUR-NEW-KEY
    export DEFAULT_MODEL_ID=openai/gpt-4o-mini      # optional override
    python scripts/test_openrouter.py "optional custom message"
"""
import asyncio
import os
import sys

# Make `import app...` work when run as `python scripts/test_openrouter.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings          # noqa: E402
from app.services.openai_service import openai_service  # noqa: E402


async def main() -> int:
    model_id = os.getenv("DEFAULT_MODEL_ID", settings.default_model_id)
    user_msg = sys.argv[1] if len(sys.argv) > 1 else (
        "A customer messages an Ethiopian shop saying 'selam, do you deliver?'. "
        "Reply warmly in one or two sentences."
    )

    print("── OpenRouter / AI path check ───────────────────────────────")
    print(f"base_url : {settings.openai_base_url or '(unset → api.openai.com)'}")
    print(f"api_key  : {'set' if settings.openai_api_key else 'MISSING'}")
    print(f"model_id : {model_id}")
    print(f"prompt   : {user_msg}")
    print("─────────────────────────────────────────────────────────────")

    if not settings.openai_api_key:
        print("ERROR: OPENAI_API_KEY is not set. export it and retry.")
        return 2

    try:
        text, tokens = await openai_service.complete(
            model_id=model_id,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt="You are a friendly assistant for an Ethiopian business.",
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ FAILED: {type(exc).__name__}: {exc}")
        print("   (401 → wrong key; model-not-found → bad model id; "
              "import error → run `pip install openai pydantic pydantic-settings`)")
        return 1

    print(f"\n✅ REPLY:\n{text}")
    print(f"\ntokens: {tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
