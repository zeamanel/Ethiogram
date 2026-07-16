# app/agents/concierge.py
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional

from app.agents.base import BaseAgent
from app.core.logging import get_logger
from app.db.models import BusinessBrainConfig
from app.services.telegram_service import MessageEnvelope  # noqa: F401  (kept for type parity)

logger = get_logger(__name__)


def _calendar_creds(child_data: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    """Pull (calendar_id, credentials_json) from the ENCRYPTED secrets only.

    These live under child_data['_secrets'] (decrypted in the webhook), never in
    plain child_data and never in the prompt.
    """
    secrets = (child_data or {}).get("_secrets") or {}
    return secrets.get("calendar_id"), secrets.get("credentials_json")


class ConciergeAgent(BaseAgent):
    """
    Specialist agent for appointment booking and calendar management.

    Booking actions use the business's real Google Calendar credentials, read
    from the encrypted child_data['_secrets']. There is NO mock/fake fallback:
    if the calendar isn't configured or the API fails, the customer is told the
    truth (no availability / booking didn't go through) — never a fake success.
    """

    agent_name = "Concierge"

    def build_system_prompt(
        self,
        brain_config: Optional[BusinessBrainConfig],
        child_data: Optional[dict],
        chunks: list[dict],
    ) -> str:
        base = super().build_system_prompt(brain_config, child_data, chunks)

        service_info = ""
        if child_data:
            services = child_data.get("services", "")
            duration = child_data.get("appointment_duration_minutes", 60)
            hours = child_data.get("business_hours", "9:00 AM - 5:00 PM, Monday to Friday")
            timezone_name = child_data.get("timezone", "Africa/Addis_Ababa")
            service_info = (
                f"\n\nAppointment details:\n"
                f"- Services offered: {services}\n"
                f"- Default duration: {duration} minutes\n"
                f"- Business hours: {hours}\n"
                f"- Timezone: {timezone_name}"
            )

        booking_addendum = (
            "\n\nYou are also a professional concierge and appointment scheduler. "
            "When a customer wants to book:\n"
            "1. Ask what service they need (if not already specified).\n"
            "2. Offer available times.\n"
            "3. Confirm the booking details before finalising.\n"
            "Always be warm, professional, and proactive." + service_info
        )
        return base + booking_addendum

    # ------------------------------------------------------------------
    # Availability (real Google Calendar free/busy)
    # ------------------------------------------------------------------

    async def get_available_slots(
        self,
        child_data: Optional[dict],
        date: datetime,
        num_slots: int = 5,
    ) -> list[dict]:
        """
        Return open slots on ``date`` from the business's real Google Calendar.
        Returns [] when the calendar isn't configured or the API fails — we
        never invent availability.
        """
        calendar_id, credentials_json = _calendar_creds(child_data)
        if not calendar_id or not credentials_json:
            logger.info("Concierge: no calendar credentials — no slots offered")
            return []

        duration = int((child_data or {}).get("appointment_duration_minutes", 60))
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._fetch_free_slots_sync,
                calendar_id, credentials_json, date, duration, num_slots,
            )
        except Exception as exc:
            logger.error("Concierge: free/busy lookup failed — no slots offered", error=str(exc))
            return []

    def _fetch_free_slots_sync(
        self,
        calendar_id: str,
        credentials_json: str,
        date: datetime,
        duration_minutes: int,
        num_slots: int,
    ) -> list[dict]:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds_data = json.loads(credentials_json)
        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        service = build("calendar", "v3", credentials=creds)

        day_start = date.replace(hour=9, minute=0, second=0, microsecond=0)
        day_end = date.replace(hour=17, minute=0, second=0, microsecond=0)

        body = {
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "items": [{"id": calendar_id}],
        }
        busy_result = service.freebusy().query(body=body).execute()
        busy_periods = busy_result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

        slots: list[dict] = []
        cursor = day_start
        step = timedelta(minutes=duration_minutes)
        while cursor + step <= day_end and len(slots) < num_slots:
            slot_end = cursor + step
            conflict = any(
                datetime.fromisoformat(b["start"]) < slot_end
                and datetime.fromisoformat(b["end"]) > cursor
                for b in busy_periods
            )
            if not conflict:
                slots.append({
                    "start": cursor.isoformat(),
                    "end": slot_end.isoformat(),
                    "label": cursor.strftime("%a %I:%M %p"),
                })
            cursor += step
        return slots

    # ------------------------------------------------------------------
    # Confirmation (real Google Calendar insert) — fail-closed
    # ------------------------------------------------------------------

    async def confirm_booking(
        self,
        child_data: Optional[dict],
        slot_start: str,
        slot_end: str,
        customer_name: str,
        customer_email: Optional[str] = None,
        service_name: Optional[str] = None,
        notes: str = "",
    ) -> dict:
        """
        Create a Google Calendar event using the business's encrypted creds.

        Returns the created event on success. On ANY failure — missing creds,
        API error, or a response with no event id — returns
        ``{"id": None, "status": "failed", "error": ...}``. It must NEVER report
        a confirmed booking unless the calendar write actually succeeded.
        """
        service_name = service_name or (child_data or {}).get("services") or "Appointment"
        calendar_id, credentials_json = _calendar_creds(child_data)

        def _fail(reason: str) -> dict:
            return {
                "id": None, "status": "failed", "error": reason,
                "summary": f"{service_name} — {customer_name}",
                "start": {"dateTime": slot_start}, "end": {"dateTime": slot_end},
            }

        if not calendar_id or not credentials_json:
            logger.warning("Concierge: confirm_booking with no calendar credentials")
            return _fail("calendar_not_configured")

        try:
            event = await asyncio.get_event_loop().run_in_executor(
                None, self._create_event_sync,
                calendar_id, credentials_json, slot_start, slot_end,
                customer_name, customer_email, service_name, notes,
            )
        except Exception as exc:
            logger.error("Concierge: calendar booking failed", error=str(exc))
            return _fail(str(exc))

        # Success ONLY if the calendar actually returned a created event id.
        if not event or not event.get("id"):
            logger.error("Concierge: calendar returned no event id", event=event)
            return _fail("no_event_id")

        logger.info("Calendar event created", agent=self.agent_name,
                    event_id=event.get("id"), customer=customer_name)
        return event

    def _create_event_sync(
        self,
        calendar_id: str,
        credentials_json: str,
        slot_start: str,
        slot_end: str,
        customer_name: str,
        customer_email: Optional[str],
        service_name: str,
        notes: str,
    ) -> dict:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds_data = json.loads(credentials_json)
        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        service = build("calendar", "v3", credentials=creds)

        event_body: dict = {
            "summary": f"{service_name} — {customer_name}",
            "description": notes,
            "start": {"dateTime": slot_start},
            "end": {"dateTime": slot_end},
        }
        if customer_email:
            event_body["attendees"] = [{"email": customer_email}]

        return service.events().insert(calendarId=calendar_id, body=event_body).execute()

    # ------------------------------------------------------------------
    # Customer-facing message + slot buttons
    # ------------------------------------------------------------------

    @staticmethod
    def booking_reply_text(result: dict, slot_label: Optional[str] = None) -> str:
        """The ONLY place that decides what the customer is told about a booking.

        Reports success strictly when the calendar write succeeded (status not
        'failed' AND a real event id). Otherwise an explicit failure — never a
        fake 'confirmed'.
        """
        succeeded = bool(result) and result.get("status") != "failed" and bool(result.get("id"))
        if succeeded:
            when = slot_label or (result.get("start") or {}).get("dateTime", "")
            tail = f" for {when}" if when else ""
            return f"✅ Your appointment is confirmed{tail}. We look forward to seeing you!"
        return (
            "⚠️ Sorry — I couldn't confirm your booking and it did not go through. "
            "Please pick another time or contact us directly to book."
        )

    def format_slots_as_buttons(self, slots: list[dict], session_key: str) -> list[list[dict]]:
        """
        Inline keyboard (one button per slot). callback_data is
        ``book_slot:<session_key>:<index>`` — the full slot details are kept
        server-side (Redis) under ``session_key`` to stay within Telegram's
        64-byte callback_data limit and avoid colons-in-ISO parsing issues.
        """
        return [
            [{"text": slot["label"], "callback_data": f"book_slot:{session_key}:{i}"}]
            for i, slot in enumerate(slots)
        ]


concierge_agent = ConciergeAgent()
