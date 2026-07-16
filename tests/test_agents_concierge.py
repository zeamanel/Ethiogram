"""Tests for app.agents.concierge.

  - default timezone is a real tz, not settings.base_url (copy-paste bug)
  - booking reads creds from child_data["_secrets"] only
  - confirm_booking NEVER reports success unless the calendar write succeeded,
    and the customer-facing message never says "confirmed" on failure
"""
from datetime import datetime, timezone

import pytest

from app.agents.concierge import ConciergeAgent, concierge_agent

# A realistic-looking but non-functional service-account creds blob.
_FAKE_CREDS = '{"type":"service_account","project_id":"x","private_key":"-----BEGIN PRIVATE KEY-----\\nMIIBADANBgkq\\n-----END PRIVATE KEY-----\\n","client_email":"x@x.iam.gserviceaccount.com","token_uri":"https://oauth2.googleapis.com/token"}'


def test_default_timezone_is_sane():
    prompt = concierge_agent.build_system_prompt(
        brain_config=None,
        child_data={"services": "haircut"},  # no "timezone" key -> use default
        chunks=[],
    )
    assert "Africa/Addis_Ababa" in prompt
    assert "http" not in prompt.lower()  # the old bug leaked base_url (a URL)


def test_explicit_timezone_is_used():
    prompt = concierge_agent.build_system_prompt(
        brain_config=None,
        child_data={"services": "haircut", "timezone": "Europe/London"},
        chunks=[],
    )
    assert "Europe/London" in prompt


# ── booking reads creds from _secrets only ──────────────────────────────────

@pytest.mark.asyncio
async def test_get_available_slots_empty_when_not_configured():
    # No _secrets -> no calendar creds -> NO fake availability.
    slots = await concierge_agent.get_available_slots(
        child_data={"services": "Haircut"}, date=datetime(2026, 6, 22, tzinfo=timezone.utc),
    )
    assert slots == []


@pytest.mark.asyncio
async def test_confirm_booking_fails_when_not_configured():
    result = await concierge_agent.confirm_booking(
        child_data={"services": "Haircut"},   # no _secrets
        slot_start="2026-06-22T09:00:00", slot_end="2026-06-22T10:00:00",
        customer_name="Test Customer",
    )
    assert result["status"] == "failed"
    assert result["id"] is None
    assert result["error"] == "calendar_not_configured"


# ── THE critical guarantee: calendar failure must NOT tell the customer "confirmed" ──

@pytest.mark.asyncio
async def test_calendar_failure_never_reports_confirmed_to_customer(monkeypatch):
    """Simulate a real Google Calendar API failure during the write and assert
    the customer-facing message does NOT say confirmed."""
    def _boom(*args, **kwargs):
        raise RuntimeError("Google Calendar API 500: backend error")

    # Force the actual calendar insert to fail.
    monkeypatch.setattr(ConciergeAgent, "_create_event_sync", _boom)

    child_data = {
        "services": "Consultation",
        "_secrets": {"calendar_id": "cal@example.com", "credentials_json": _FAKE_CREDS},
    }
    result = await concierge_agent.confirm_booking(
        child_data=child_data,
        slot_start="2026-06-22T09:00:00", slot_end="2026-06-22T10:00:00",
        customer_name="Abebe", service_name="Consultation",
    )

    # 1. The result object signals failure, not a confirmed event.
    assert result["status"] == "failed"
    assert result["id"] is None

    # 2. The CUSTOMER-FACING message must not claim success.
    msg = concierge_agent.booking_reply_text(result)
    assert "confirm" not in msg.lower().replace("couldn't confirm", "")  # no "confirmed"
    assert "did not go through" in msg.lower()


def test_booking_reply_text_success_only_with_real_event_id():
    # A real created event -> confirmed message.
    ok = concierge_agent.booking_reply_text(
        {"id": "evt_123", "status": "confirmed", "start": {"dateTime": "2026-06-22T09:00:00"}}
    )
    assert "confirmed" in ok.lower()

    # A "confirmed"-looking status but NO id -> still treated as failure.
    spoof = concierge_agent.booking_reply_text({"id": None, "status": "confirmed"})
    assert "confirmed" not in spoof.lower().replace("couldn't confirm", "")
    assert "did not go through" in spoof.lower()


def test_format_slots_as_buttons_uses_session_key():
    slots = [
        {"start": "2026-06-22T09:00:00", "end": "2026-06-22T10:00:00", "label": "Mon 09:00 AM"},
        {"start": "2026-06-22T10:00:00", "end": "2026-06-22T11:00:00", "label": "Mon 10:00 AM"},
    ]
    rows = concierge_agent.format_slots_as_buttons(slots, session_key="abcd1234")
    assert rows[0][0]["callback_data"] == "book_slot:abcd1234:0"
    assert rows[1][0]["callback_data"] == "book_slot:abcd1234:1"
    assert rows[0][0]["text"] == "Mon 09:00 AM"
