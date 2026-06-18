"""Tests for app.agents.concierge (STEP 2 bug fixes).

  - default timezone is a real tz, not settings.base_url (copy-paste bug)
  - confirm_booking NEVER reports success when the calendar write fails
"""
import pytest

from app.agents.concierge import concierge_agent


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


@pytest.mark.asyncio
async def test_confirm_booking_failure_does_not_report_success():
    # Invalid credentials JSON makes the calendar write raise; the result must
    # signal failure, never a confirmed booking.
    result = await concierge_agent.confirm_booking(
        calendar_id="cal@example.com",
        credentials_json="not-valid-json",
        slot_start="2026-06-20T09:00:00",
        slot_end="2026-06-20T10:00:00",
        customer_name="Test Customer",
        customer_email=None,
        service_name="Consultation",
    )
    assert result["status"] == "failed"
    assert result["status"] != "confirmed"
    assert result["id"] is None
    assert "error" in result
