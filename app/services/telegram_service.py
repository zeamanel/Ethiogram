# app/services/telegram_service.py
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.core.config import settings
from app.core.exceptions import TelegramAPIError
from app.core.logging import get_logger

logger = get_logger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


def _timeout() -> httpx.Timeout:
    # Read from settings so it can be raised for local dev over a slow VPN
    # (TELEGRAM_CONNECT_TIMEOUT / TELEGRAM_READ_TIMEOUT) without code changes.
    return httpx.Timeout(settings.telegram_read_timeout, connect=settings.telegram_connect_timeout)


@dataclass
class MessageEnvelope:
    """Platform-agnostic representation of an incoming message."""
    platform: str                          # "telegram" | "whatsapp"
    business_id: Optional[str]            # resolved after DB lookup
    bot_id: Optional[str]                 # resolved after DB lookup
    token_hash: str                        # used for DB lookup
    customer_id: str                       # platform-specific user/chat id
    customer_name: Optional[str]
    customer_username: Optional[str]
    text: Optional[str]
    media_type: Optional[str]             # "photo" | "document" | "voice" | None
    media_url: Optional[str]
    media_file_id: Optional[str]          # Telegram file_id for download
    message_id: int
    timestamp: datetime
    raw: dict = field(repr=False)         # original update payload
    chat_type: str = "private"            # private | group | supergroup | channel


class TelegramService:
    """
    Thin async wrapper around the Telegram Bot API.
    All methods accept the raw bot token so the service stays stateless —
    the caller is responsible for decrypting the token before passing it.
    """

    def _url(self, token: str, method: str) -> str:
        return f"{_TELEGRAM_API_BASE.format(token=token)}/{method}"

    async def _post(self, token: str, method: str, payload: dict) -> dict:
        url = self._url(token, method)
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.post(url, json=payload)
        data = response.json()
        if not data.get("ok"):
            error_desc = data.get("description", "unknown error")
            logger.warning(
                f"Telegram API error on {method}",
                method=method,
                error=error_desc,
                error_code=data.get("error_code"),
            )
            raise TelegramAPIError(error_desc)
        return data.get("result", {})

    async def _get(self, token: str, method: str) -> dict:
        url = self._url(token, method)
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.get(url)
        data = response.json()
        if not data.get("ok"):
            raise TelegramAPIError(data.get("description", "unknown error"))
        return data.get("result", {})

    # ------------------------------------------------------------------
    # Bot info & webhook management
    # ------------------------------------------------------------------

    async def get_me(self, token: str) -> dict:
        """Validate the token and return bot info. Raises TelegramAPIError if invalid."""
        return await self._get(token, "getMe")

    async def set_webhook(
        self,
        token: str,
        url: str,
        secret_token: str,
        allowed_updates: Optional[list[str]] = None,
        max_connections: int = 100,
    ) -> bool:
        payload: dict = {
            "url": url,
            "secret_token": secret_token,
            "max_connections": max_connections,
            "allowed_updates": allowed_updates or ["message", "callback_query"],
            "drop_pending_updates": True,
        }
        await self._post(token, "setWebhook", payload)
        logger.info("Webhook registered", webhook_url=url)
        return True

    async def delete_webhook(self, token: str, drop_pending: bool = False) -> bool:
        await self._post(token, "deleteWebhook", {"drop_pending_updates": drop_pending})
        return True

    async def get_webhook_info(self, token: str) -> dict:
        return await self._get(token, "getWebhookInfo")

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    async def send_message(
        self,
        token: str,
        chat_id: int | str,
        text: str,
        parse_mode: str = "HTML",
        reply_to_message_id: Optional[int] = None,
        disable_web_page_preview: bool = True,
    ) -> dict:
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        return await self._post(token, "sendMessage", payload)

    async def send_message_with_buttons(
        self,
        token: str,
        chat_id: int | str,
        text: str,
        buttons: list[list[dict]],
        parse_mode: str = "HTML",
    ) -> dict:
        """
        buttons is a list of rows; each row is a list of button dicts.
        Inline button: {"text": "Label", "callback_data": "data"}
        URL button:    {"text": "Label", "url": "https://..."}
        """
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": {"inline_keyboard": buttons},
        }
        return await self._post(token, "sendMessage", payload)

    async def send_typing_action(self, token: str, chat_id: int | str) -> None:
        try:
            await self._post(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception as exc:
            # Fire-and-forget: a network/timeout/API error on the typing
            # indicator must never crash the webhook (which would 500 ->
            # Telegram retry -> re-charge). Swallow everything.
            logger.debug("send_typing_action failed (non-critical)", error=str(exc))

    async def send_photo(
        self,
        token: str,
        chat_id: int | str,
        photo_url: str,
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
    ) -> dict:
        payload: dict = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption
            payload["parse_mode"] = parse_mode
        return await self._post(token, "sendPhoto", payload)

    async def send_document(
        self,
        token: str,
        chat_id: int | str,
        document_url: str,
        caption: Optional[str] = None,
    ) -> dict:
        payload: dict = {"chat_id": chat_id, "document": document_url}
        if caption:
            payload["caption"] = caption
        return await self._post(token, "sendDocument", payload)

    async def edit_message_text(
        self,
        token: str,
        chat_id: int | str,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
    ) -> dict:
        return await self._post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        })

    async def answer_callback_query(
        self,
        token: str,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        payload: dict = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        await self._post(token, "answerCallbackQuery", payload)
        return True

    async def get_file(self, token: str, file_id: str) -> dict:
        return await self._post(token, "getFile", {"file_id": file_id})

    async def get_file_download_url(self, token: str, file_id: str) -> str:
        file_info = await self.get_file(token, file_id)
        file_path = file_info.get("file_path", "")
        return f"https://api.telegram.org/file/bot{token}/{file_path}"

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    def verify_webhook_signature(
        self, secret_token: str, provided_token: str
    ) -> bool:
        """
        Verify the ``X-Telegram-Bot-Api-Secret-Token`` header.

        Telegram returns the secret_token (set via setWebhook) verbatim in this
        header — it is NOT an HMAC of the request body. Compare in constant time.
        """
        if not provided_token:
            return False
        return hmac.compare_digest(secret_token, provided_token)

    # ------------------------------------------------------------------
    # Update parsing → MessageEnvelope
    # ------------------------------------------------------------------

    def parse_incoming_update(self, body: dict, token_hash: str) -> Optional[MessageEnvelope]:
        """
        Convert a raw Telegram Update dict into a MessageEnvelope.
        Returns None for updates we deliberately ignore (e.g. edited messages,
        channel posts, inline queries — anything without a processable message).
        """
        message = body.get("message") or body.get("callback_query", {}).get("message")
        if not message:
            return None

        # callback_query wraps the message; extract the text from it differently
        is_callback = "callback_query" in body
        callback_data: Optional[str] = None
        if is_callback:
            callback_data = body["callback_query"].get("data")
            sender = body["callback_query"].get("from", {})
        else:
            sender = message.get("from", {})

        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "private")
        message_id: int = message.get("message_id", 0)

        # Resolve the conversation identity. In a group/supergroup the bot serves
        # the GROUP (replies go to the group, one conversation per group), so the
        # chat id is the identity. In private chats the chat id == the user id.
        if chat_type in ("group", "supergroup"):
            customer_id = chat_id
        else:
            customer_id = str(sender.get("id", chat_id))
        first = sender.get("first_name", "")
        last = sender.get("last_name", "")
        customer_name = f"{first} {last}".strip() or None
        customer_username = sender.get("username")

        # Resolve text
        if is_callback:
            text = callback_data
            media_type = None
            media_url = None
            media_file_id = None
        else:
            text = message.get("text") or message.get("caption")
            media_type, media_url, media_file_id = self._extract_media(message)

        # Timestamp
        ts_unix: int = message.get("date", 0)
        timestamp = datetime.fromtimestamp(ts_unix, tz=timezone.utc)

        return MessageEnvelope(
            platform="telegram",
            business_id=None,       # resolved by webhook handler after DB lookup
            bot_id=None,            # resolved by webhook handler after DB lookup
            token_hash=token_hash,
            customer_id=customer_id,
            customer_name=customer_name,
            customer_username=customer_username,
            text=text,
            media_type=media_type,
            media_url=media_url,
            media_file_id=media_file_id,
            message_id=message_id,
            timestamp=timestamp,
            raw=body,
            chat_type=chat_type,
        )

    def _extract_media(self, message: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (media_type, media_url, file_id) from a message dict."""
        if "photo" in message:
            # Telegram sends an array ordered by resolution; last = largest
            photos = message["photo"]
            best = photos[-1] if photos else {}
            return "photo", None, best.get("file_id")

        if "document" in message:
            doc = message["document"]
            return "document", None, doc.get("file_id")

        if "voice" in message:
            return "voice", None, message["voice"].get("file_id")

        if "audio" in message:
            return "audio", None, message["audio"].get("file_id")

        if "video" in message:
            return "video", None, message["video"].get("file_id")

        if "sticker" in message:
            return "sticker", None, message["sticker"].get("file_id")

        if "location" in message:
            loc = message["location"]
            url = f"geo:{loc.get('latitude')},{loc.get('longitude')}"
            return "location", url, None

        return None, None, None


telegram_service = TelegramService()
