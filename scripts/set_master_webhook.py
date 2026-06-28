"""Register (or re-register) the master/platform bot's Telegram webhook.

The webhook handler (app/api/webhooks.py) routes every update by the token_hash
embedded in the URL path:

    {BASE_URL}/webhook/{hash_bot_token(MASTER_BOT_TOKEN)}

hash_bot_token() mixes in WEBHOOK_SECRET_SALT, so this script MUST run with the
SAME MASTER_BOT_TOKEN, WEBHOOK_SECRET_SALT and BASE_URL the live service uses —
otherwise the registered hash won't match what the server computes and updates
fall through to the (empty) business-bot lookup, and the bot stays silent.

Run it with the production secrets in the environment, e.g.:

    export MASTER_BOT_TOKEN=$(gcloud secrets versions access latest --secret=ethiogram-master-bot-token)
    export WEBHOOK_SECRET_SALT=$(gcloud secrets versions access latest --secret=ethiogram-webhook-salt)
    export BASE_URL=https://ethiogram-api-760742977917.us-central1.run.app
    python -m scripts.set_master_webhook

It prints the bot identity, the URL it registered, and what Telegram reports
back (including any last_error_message), so it doubles as a diagnostic.
"""
import asyncio

from app.core.config import settings
from app.core.security import generate_webhook_secret, hash_bot_token
from app.services.telegram_service import telegram_service


async def main() -> None:
    token = settings.master_bot_token
    if not token:
        raise SystemExit("MASTER_BOT_TOKEN is not set in the environment.")
    base = (settings.base_url or "").rstrip("/")
    if not base:
        raise SystemExit("BASE_URL is not set in the environment.")

    url = f"{base}/webhook/{hash_bot_token(token)}"
    secret = generate_webhook_secret()

    me = await telegram_service.get_me(token)
    await telegram_service.set_webhook(token, url, secret)
    info = await telegram_service.get_webhook_info(token)

    print(f"Bot:                 @{me.get('username')} (id={me.get('id')})")
    print(f"Registered URL:      {url}")
    print(f"Telegram reports:    {info.get('url')}")
    print(f"pending_updates:     {info.get('pending_update_count')}")
    if info.get("last_error_message"):
        print(f"last_error_message:  {info.get('last_error_message')}")
    print("\nDone. Send /start to the bot to confirm the menu appears.")


if __name__ == "__main__":
    asyncio.run(main())
