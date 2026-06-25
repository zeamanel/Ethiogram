# workers/seed_agents.py
"""
Seed the marketplace with Ethiogram's first-party specialist agents.

The marketplace ships empty — `SELECT * FROM agents` returns nothing — so no
business can deploy a specialist and the dashboard's "Active Agents" is always
empty. This script publishes the two agents the codebase actually has working
specialist classes for:

  • Booking Concierge   → app.agents.concierge.ConciergeAgent  (calendar booking)
  • Receipt Assistant   → app.agents.accountant.AccountantAgent (receipt OCR)

General Q&A needs no marketplace agent — BaseAgent already answers by default.

Why a script and not SQL: `agents.encrypted_system_prompt` is Fernet-encrypted
with the app's ENCRYPTION_KEY, so the ciphertext can only be produced in-process.

Idempotent: re-running updates the existing rows (matched by name) instead of
creating duplicates. Safe to run after every deploy.

Run as a one-off Cloud Run Job (reuses the API image's ENCRYPTION_KEY + DB):
    gcloud run jobs execute seed-agents --region <region>
Or locally against a reachable DB:
    python -m workers.seed_agents
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from app.core.security import encrypt_agent_prompt
from app.db.models import Agent, AgentStatus, CreatorProfile, User, UserRole
from app.db.session import connect_db, disconnect_db, get_db_context

configure_logging()
logger = get_logger(__name__)

# The first-party publisher identity. Stable email → idempotent lookup.
_PLATFORM_EMAIL = "platform@ethiogram.com"
_PLATFORM_NAME = "Ethiogram"


# ── First-party agent catalog ────────────────────────────────────────────────
# `category`/`tags`/`capabilities` must contain the keywords that
# webhooks._agent_type_for uses to route a deployed child to its specialist
# class (_CONCIERGE_KEYWORDS / _ACCOUNTANT_KEYWORDS), or it falls back to
# BaseAgent and the specialist behaviour never runs.

SEED_AGENTS = [
    {
        "name": "Booking Concierge",
        "tagline": "Let customers book appointments in chat — synced to your calendar.",
        "description": (
            "A booking specialist that offers your real availability, takes "
            "appointments, and writes them straight to your Google Calendar. "
            "It never invents slots and never confirms a booking unless the "
            "calendar write actually succeeded."
        ),
        "category": "Concierge & Booking",
        "tags": ["booking", "appointment", "calendar", "scheduling"],
        "capabilities": ["appointment scheduling", "calendar booking"],
        "system_prompt": (
            "You are a professional booking concierge for this business. Help "
            "customers find a suitable time and book an appointment. Only offer "
            "times you are given as available. Be warm, concise, and confirm the "
            "service, date, and time clearly. Never claim a booking is confirmed "
            "unless the system tells you the calendar event was created."
        ),
        "child_schema": {
            "fields": [
                {"key": "services", "label": "Services offered", "type": "multiline",
                 "placeholder": "e.g. Haircut, Beard trim, Hair colour",
                 "help": "What customers can book. Listed to the customer when they ask."},
                {"key": "appointment_duration_minutes", "label": "Default appointment length (minutes)",
                 "type": "text", "placeholder": "60"},
                {"key": "business_hours", "label": "Business hours", "type": "text",
                 "placeholder": "9:00 AM - 5:00 PM, Monday to Friday"},
                {"key": "timezone", "label": "Timezone", "type": "text",
                 "placeholder": "Africa/Addis_Ababa"},
            ],
            # Sensitive fields — collected via the write-only secrets editor,
            # Fernet-encrypted into child_agents.child_secrets, and surfaced to
            # the agent under _secrets (calendar_id, credentials_json). Keys MUST
            # match what ConciergeAgent._calendar_creds reads.
            "secret_fields": [
                {"key": "calendar_id", "label": "Google Calendar ID", "type": "text",
                 "placeholder": "you@gmail.com or ...@group.calendar.google.com",
                 "help": "The calendar bookings are written to. Share it with the "
                         "service-account email (Editor access)."},
                {"key": "credentials_json", "label": "Service-account key (JSON)", "type": "multiline",
                 "placeholder": '{ "type": "service_account", ... }',
                 "help": "Paste the full service-account key JSON from Google Cloud. "
                         "Stored encrypted; never shown again."},
            ],
        },
        "setup_guide": (
            "Fill in your services, hours and timezone. To enable live calendar "
            "booking, your Google Calendar service-account credentials must be "
            "connected (calendar_id + credentials_json). Until they are, the "
            "agent answers booking questions but cannot write events."
        ),
        "price_etg": 0,
    },
    {
        "name": "Receipt & Expense Assistant",
        "tagline": "Customers snap a receipt; it reads the vendor, date and total.",
        "description": (
            "An accounting specialist that runs OCR on receipt photos and "
            "documents, extracts the vendor, date, total and line items, and "
            "answers expense questions about them. No configuration required."
        ),
        "category": "Accounting",
        "tags": ["receipt", "ocr", "expense", "bookkeeping", "invoice"],
        "capabilities": ["receipt OCR", "expense tracking"],
        "system_prompt": (
            "You are a meticulous bookkeeping assistant. When a receipt's text is "
            "provided, extract the vendor, date, total amount, currency, and line "
            "items, and present them clearly. Answer expense and finance questions "
            "accurately. If the text is unreadable, ask for a clearer photo rather "
            "than guessing."
        ),
        "child_schema": None,   # the specialist reads no per-business config
        "setup_guide": (
            "No setup needed. Customers send a photo or PDF of a receipt and the "
            "agent extracts the details automatically."
        ),
        "price_etg": 0,
    },
]


async def _ensure_publisher(db) -> CreatorProfile:
    """Find-or-create the first-party platform user + trusted creator profile.
    is_trusted=True so published agents go straight to `live`."""
    user = (await db.execute(
        select(User).where(User.email == _PLATFORM_EMAIL)
    )).scalar_one_or_none()
    if user is None:
        user = User(email=_PLATFORM_EMAIL, full_name=_PLATFORM_NAME,
                    role=UserRole.creator, is_active=True)
        db.add(user)
        await db.flush()
        logger.info("Created platform user", user_id=str(user.id))

    profile = (await db.execute(
        select(CreatorProfile).where(CreatorProfile.user_id == user.id)
    )).scalar_one_or_none()
    if profile is None:
        profile = CreatorProfile(user_id=user.id, display_name=_PLATFORM_NAME,
                                 is_trusted=True, is_verified=True)
        db.add(profile)
        await db.flush()
        logger.info("Created platform creator profile", profile_id=str(profile.id))
    return profile


async def _upsert_agent(db, profile: CreatorProfile, spec: dict) -> str:
    """Insert the agent, or update the existing row matched by name (idempotent)."""
    encrypted, key_ref = encrypt_agent_prompt(spec["system_prompt"], "seed")
    existing = (await db.execute(
        select(Agent).where(
            Agent.creator_id == profile.id, Agent.name == spec["name"]
        )
    )).scalar_one_or_none()

    if existing is None:
        agent = Agent(
            creator_id=profile.id,
            name=spec["name"],
            tagline=spec["tagline"],
            description=spec["description"],
            category=spec["category"],
            tags=spec["tags"],
            capabilities=spec["capabilities"],
            encrypted_system_prompt=encrypted,
            encryption_key_ref=key_ref,
            child_schema=spec["child_schema"],
            setup_guide=spec["setup_guide"],
            price_etg=spec["price_etg"],
            status=AgentStatus.live,
        )
        db.add(agent)
        await db.flush()
        logger.info("Seeded agent", name=spec["name"], agent_id=str(agent.id))
        return "created"

    # update mutable fields in place — keeps the same id (and any deployments)
    existing.tagline = spec["tagline"]
    existing.description = spec["description"]
    existing.category = spec["category"]
    existing.tags = spec["tags"]
    existing.capabilities = spec["capabilities"]
    existing.encrypted_system_prompt = encrypted
    existing.encryption_key_ref = key_ref
    existing.child_schema = spec["child_schema"]
    existing.setup_guide = spec["setup_guide"]
    existing.price_etg = spec["price_etg"]
    if existing.status != AgentStatus.live:
        existing.status = AgentStatus.live
    logger.info("Updated existing agent", name=spec["name"], agent_id=str(existing.id))
    return "updated"


async def seed() -> dict:
    summary = {"created": 0, "updated": 0}
    async with get_db_context() as db:        # commits on clean exit
        profile = await _ensure_publisher(db)
        for spec in SEED_AGENTS:
            result = await _upsert_agent(db, profile, spec)
            summary[result] += 1
    logger.info("Agent seeding complete", **summary)
    return summary


async def main() -> None:
    await connect_db()
    try:
        summary = await seed()
        print(f"Seeded agents — created={summary['created']} updated={summary['updated']}")
    finally:
        await disconnect_db()


if __name__ == "__main__":
    asyncio.run(main())
