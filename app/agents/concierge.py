# app/agents/concierge.py
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResponse, BaseAgent
from app.core.logging import get_logger
from app.db.models import BusinessBrainConfig
from app.services.telegram_service import MessageEnvelope

logger = get_logger(__name__)

# Day names for slot formatting
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ConciergeAgent(BaseAgent):
    """
    Specialist agent for appointment booking and calendar management.

    Extended behaviour over BaseAgent:
    - Detects booking intent and queries Google Calendar for availability
    - Presents available slots to the customer as inline buttons
    - Confirms bookings and creates Calendar events
    - Sends reminder details back to the customer
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
            "2. Ask for their preferred date and time.\n"
            "3. Confirm the booking details before finalising.\n"
            "4. Once confirmed, tell the customer their appointment is booked "
            "and provide a brief summary.\n"
            "Always be warm, professional, and proactive." + service_info
        )
        return base + booking_addendum

    async def get_available_slots(
        self,
        calendar_id: str,
        credentials_json: str,
        date: datetime,
        duration_minutes: int = 60,
        num_slots: int = 5,
    ) -> list[dict]:
        """
        Query Google Calendar free/busy and return open slots on `date`.
        Returns list of {"start": ISO str, "end": ISO str, "label": "Mon 9:00 AM"}
        """
        try:
            slots = await self._fetch_free_slots(
                calendar_id, credentials_json, date, duration_minutes, num_slots
            )
            return slots
        except Exception as exc:
            logger.error(
                "Failed to fetch calendar slots",
                error=str(exc),
                calendar_id=calendar_id,
            )
            return self._generate_mock_slots(date, duration_minutes, num_slots)

    async def _fetch_free_slots(
        self,
        calendar_id: str,
        credentials_json: str,
        date: datetime,
        duration_minutes: int,
        num_slots: int,
    ) -> list[dict]:
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._fetch_free_slots_sync,
            calendar_id,
            credentials_json,
            date,
            duration_minutes,
            num_slots,
        )

    def _fetch_free_slots_sync(
        self,
        calendar_id: str,
        credentials_json: str,
        date: datetime,
        duration_minutes: int,
        num_slots: int,
    ) -> list[dict]:
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError("google-api-python-client not installed")

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

        # Walk through the day in `duration_minutes` increments, skip busy blocks
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

    def _generate_mock_slots(
        self, date: datetime, duration_minutes: int, num_slots: int
    ) -> list[dict]:
        """Fallback when Calendar API is unavailable — return placeholder slots."""
        slots = []
        cursor = date.replace(hour=9, minute=0, second=0, microsecond=0)
        step = timedelta(minutes=duration_minutes)
        for _ in range(num_slots):
            end = cursor + step
            slots.append({
                "start": cursor.isoformat(),
                "end": end.isoformat(),
                "label": cursor.strftime("%a %I:%M %p"),
            })
            cursor = end
        return slots

    async def confirm_booking(
        self,
        calendar_id: str,
        credentials_json: str,
        slot_start: str,
        slot_end: str,
        customer_name: str,
        customer_email: Optional[str],
        service_name: str,
        notes: str = "",
    ) -> dict:
        """
        Create a Google Calendar event and return the event dict.

        On success returns the Calendar event (which carries ``status`` from
        Google, e.g. "confirmed"). On failure returns
        ``{"status": "failed", "error": <message>, "id": None}`` — it must
        NEVER report a confirmed booking when the calendar write did not
        succeed, so the caller can tell the customer it didn't go through.
        """
        try:
            import asyncio
            event = await asyncio.get_event_loop().run_in_executor(
                None,
                self._create_event_sync,
                calendar_id,
                credentials_json,
                slot_start,
                slot_end,
                customer_name,
                customer_email,
                service_name,
                notes,
            )
            logger.info(
                "Calendar event created",
                agent=self.agent_name,
                event_id=event.get("id"),
                customer=customer_name,
            )
            return event
        except Exception as exc:
            logger.error("Calendar booking failed", error=str(exc))
            return {
                "id": None,
                "status": "failed",
                "error": str(exc),
                "summary": f"{service_name} — {customer_name}",
                "start": {"dateTime": slot_start},
                "end": {"dateTime": slot_end},
            }

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

    def format_slots_as_buttons(self, slots: list[dict]) -> list[list[dict]]:
        """
        Convert slot list into Telegram inline keyboard rows (one button per slot).
        callback_data encodes the slot index for retrieval.
        """
        return [
            [{"text": slot["label"], "callback_data": f"book_slot:{i}:{slot['start']}"}]
            for i, slot in enumerate(slots)
        ]


concierge_agent = ConciergeAgent()
