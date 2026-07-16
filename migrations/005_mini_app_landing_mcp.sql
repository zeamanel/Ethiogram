-- migrations/005_mini_app_landing_mcp.sql
-- Mini App configs, landing pages, live commerce, MCP tool registry

BEGIN;

-- ── mini_app_configs ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mini_app_configs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id         UUID NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,
    primary_color       TEXT NOT NULL DEFAULT '#2563EB',
    secondary_color     TEXT NOT NULL DEFAULT '#1E40AF',
    font_family         TEXT NOT NULL DEFAULT 'Inter',
    logo_url            TEXT,
    banner_url          TEXT,
    welcome_message     TEXT,
    show_products       BOOLEAN NOT NULL DEFAULT TRUE,
    show_orders         BOOLEAN NOT NULL DEFAULT TRUE,
    show_chat           BOOLEAN NOT NULL DEFAULT TRUE,
    show_appointments   BOOLEAN NOT NULL DEFAULT FALSE,
    custom_links        JSONB NOT NULL DEFAULT '[]',
    social_links        JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mini_app_configs_business_id ON mini_app_configs(business_id);

-- ── landing_pages ─────────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE landing_page_status AS ENUM ('draft', 'published', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS landing_pages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    meta_description TEXT,
    hero_image_url  TEXT,
    sections        JSONB NOT NULL DEFAULT '[]',
    seo_tags        JSONB NOT NULL DEFAULT '{}',
    status          landing_page_status NOT NULL DEFAULT 'draft',
    published_at    TIMESTAMPTZ,
    view_count      BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_landing_pages_business_id ON landing_pages(business_id);
CREATE INDEX IF NOT EXISTS idx_landing_pages_slug ON landing_pages(slug);
CREATE INDEX IF NOT EXISTS idx_landing_pages_status ON landing_pages(status);

-- ── live_commerce_sessions ────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE live_session_status AS ENUM ('scheduled', 'live', 'ended');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS live_commerce_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT,
    status          live_session_status NOT NULL DEFAULT 'scheduled',
    scheduled_at    TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    stream_url      TEXT,
    featured_products JSONB NOT NULL DEFAULT '[]',
    viewer_count    INT NOT NULL DEFAULT 0,
    peak_viewers    INT NOT NULL DEFAULT 0,
    total_orders    INT NOT NULL DEFAULT 0,
    total_revenue   NUMERIC(14,2) NOT NULL DEFAULT 0,
    firebase_room_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_live_sessions_business_id ON live_commerce_sessions(business_id);
CREATE INDEX IF NOT EXISTS idx_live_sessions_status ON live_commerce_sessions(status);
CREATE INDEX IF NOT EXISTS idx_live_sessions_scheduled_at ON live_commerce_sessions(scheduled_at);

-- ── mcp_tool_registry ─────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE mcp_tool_status AS ENUM ('active', 'deprecated', 'beta');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS mcp_tool_registry (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tool_name       TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    description     TEXT,
    category        TEXT NOT NULL DEFAULT 'general',
    status          mcp_tool_status NOT NULL DEFAULT 'active',
    schema          JSONB NOT NULL DEFAULT '{}',
    etg_cost        INT NOT NULL DEFAULT 0,
    requires_auth   BOOLEAN NOT NULL DEFAULT FALSE,
    auth_scopes     JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mcp_tool_registry_category ON mcp_tool_registry(category);
CREATE INDEX IF NOT EXISTS idx_mcp_tool_registry_status ON mcp_tool_registry(status);

-- ── business_mcp_tools (enabled tools per business) ──────────────────────────
CREATE TABLE IF NOT EXISTS business_mcp_tools (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    tool_id     UUID NOT NULL REFERENCES mcp_tool_registry(id) ON DELETE CASCADE,
    config      JSONB NOT NULL DEFAULT '{}',
    is_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    enabled_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_business_mcp_tools_unique
    ON business_mcp_tools(business_id, tool_id);
CREATE INDEX IF NOT EXISTS idx_business_mcp_tools_business_id ON business_mcp_tools(business_id);

-- ── appointment_slots ─────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE slot_status AS ENUM ('available', 'booked', 'cancelled', 'blocked');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS appointment_slots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    bot_id          UUID REFERENCES bots(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    customer_name   TEXT,
    customer_phone  TEXT,
    service_name    TEXT,
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ NOT NULL,
    status          slot_status NOT NULL DEFAULT 'available',
    calendar_event_id TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_appointment_slots_business_id ON appointment_slots(business_id);
CREATE INDEX IF NOT EXISTS idx_appointment_slots_starts_at ON appointment_slots(starts_at);
CREATE INDEX IF NOT EXISTS idx_appointment_slots_status ON appointment_slots(status);

COMMIT;
