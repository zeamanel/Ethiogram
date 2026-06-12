-- migrations/003_agents_marketplace.sql
-- Father/Child agent marketplace: agents, unlocks, trials, reviews, deployed agents

BEGIN;

-- ── Enums ─────────────────────────────────────────────────────────────────────
CREATE TYPE agent_status AS ENUM ('draft', 'pending_review', 'approved', 'rejected', 'suspended');
CREATE TYPE agent_category AS ENUM (
    'customer_support', 'sales', 'bookings', 'accountant',
    'inventory', 'hr', 'marketing', 'general'
);
CREATE TYPE agent_unlock_status AS ENUM ('active', 'expired', 'refunded');
CREATE TYPE trial_status AS ENUM ('active', 'expired', 'converted');

-- ── father_agents (marketplace templates) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS father_agents (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    creator_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    tagline                 TEXT NOT NULL,
    description             TEXT,
    category                agent_category NOT NULL DEFAULT 'general',
    status                  agent_status NOT NULL DEFAULT 'draft',
    -- system prompt is AES-encrypted; never exposed via API
    system_prompt_encrypted TEXT NOT NULL,
    capabilities            JSONB NOT NULL DEFAULT '[]',
    supported_languages     JSONB NOT NULL DEFAULT '["en"]',
    icon_url                TEXT,
    preview_screenshot_urls JSONB NOT NULL DEFAULT '[]',
    price_etg               INT NOT NULL DEFAULT 0,
    is_featured             BOOLEAN NOT NULL DEFAULT FALSE,
    is_free                 BOOLEAN NOT NULL DEFAULT FALSE,
    total_unlocks           INT NOT NULL DEFAULT 0,
    avg_rating              NUMERIC(3,2) NOT NULL DEFAULT 0,
    rating_count            INT NOT NULL DEFAULT 0,
    trial_duration_days     INT NOT NULL DEFAULT 15,
    reviewed_by_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at             TIMESTAMPTZ,
    review_notes            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_father_agents_creator_id ON father_agents(creator_id);
CREATE INDEX IF NOT EXISTS idx_father_agents_status ON father_agents(status);
CREATE INDEX IF NOT EXISTS idx_father_agents_category ON father_agents(category);
CREATE INDEX IF NOT EXISTS idx_father_agents_is_featured ON father_agents(is_featured) WHERE is_featured = TRUE;
CREATE INDEX IF NOT EXISTS idx_father_agents_deleted_at ON father_agents(deleted_at) WHERE deleted_at IS NULL;

-- ── child_agents (deployed per-business instances) ───────────────────────────
CREATE TABLE IF NOT EXISTS child_agents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    father_agent_id UUID NOT NULL REFERENCES father_agents(id) ON DELETE CASCADE,
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    bot_id          UUID REFERENCES bots(id) ON DELETE SET NULL,
    -- business-specific customisation layer (NOT the encrypted system prompt)
    child_data      JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    trial_ends_at   TIMESTAMPTZ,
    unlock_id       UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_child_agents_unique
    ON child_agents(father_agent_id, business_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_child_agents_business_id ON child_agents(business_id);
CREATE INDEX IF NOT EXISTS idx_child_agents_father_agent_id ON child_agents(father_agent_id);
CREATE INDEX IF NOT EXISTS idx_child_agents_bot_id ON child_agents(bot_id);

-- ── agent_unlocks ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_unlocks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    father_agent_id UUID NOT NULL REFERENCES father_agents(id) ON DELETE CASCADE,
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    unlocked_by_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    etg_paid        INT NOT NULL DEFAULT 0,
    status          agent_unlock_status NOT NULL DEFAULT 'active',
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_unlocks_unique
    ON agent_unlocks(father_agent_id, business_id);
CREATE INDEX IF NOT EXISTS idx_agent_unlocks_business_id ON agent_unlocks(business_id);
CREATE INDEX IF NOT EXISTS idx_agent_unlocks_father_agent_id ON agent_unlocks(father_agent_id);

-- Add FK from child_agents to agent_unlocks after both tables exist
ALTER TABLE child_agents
    ADD CONSTRAINT fk_child_agents_unlock_id
    FOREIGN KEY (unlock_id) REFERENCES agent_unlocks(id) ON DELETE SET NULL;

-- ── agent_trials ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_trials (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    father_agent_id         UUID NOT NULL REFERENCES father_agents(id) ON DELETE CASCADE,
    business_id             UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    started_by_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status                  trial_status NOT NULL DEFAULT 'active',
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ NOT NULL,
    warning_sent_at         TIMESTAMPTZ,
    critical_warning_sent_at TIMESTAMPTZ,
    expired_notification_sent_at TIMESTAMPTZ,
    converted_at            TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_trials_unique
    ON agent_trials(father_agent_id, business_id);
CREATE INDEX IF NOT EXISTS idx_agent_trials_business_id ON agent_trials(business_id);
CREATE INDEX IF NOT EXISTS idx_agent_trials_status ON agent_trials(status);
CREATE INDEX IF NOT EXISTS idx_agent_trials_expires_at ON agent_trials(expires_at);

-- ── agent_reviews ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_reviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    father_agent_id UUID NOT NULL REFERENCES father_agents(id) ON DELETE CASCADE,
    reviewer_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    unlock_id       UUID REFERENCES agent_unlocks(id) ON DELETE SET NULL,
    rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_text     TEXT,
    is_verified_purchase BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_reviews_unique
    ON agent_reviews(father_agent_id, reviewer_id);
CREATE INDEX IF NOT EXISTS idx_agent_reviews_father_agent_id ON agent_reviews(father_agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_reviews_rating ON agent_reviews(rating);

COMMIT;
