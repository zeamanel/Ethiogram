-- migrations/004_token_economy.sql
-- ETG token economy: wallets, transactions, usage events, pricing, escrow

BEGIN;

-- ── Enums ─────────────────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE transaction_type AS ENUM (
      'purchase', 'bonus', 'referral', 'grant',
      'charge', 'refund', 'escrow_hold', 'escrow_release'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE escrow_status AS ENUM ('holding', 'released', 'disputed', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── token_wallets ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS token_wallets (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id         UUID NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,
    balance             BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
    total_purchased     BIGINT NOT NULL DEFAULT 0,
    total_spent         BIGINT NOT NULL DEFAULT 0,
    subscription_plan   subscription_plan NOT NULL DEFAULT 'free',
    auto_recharge       BOOLEAN NOT NULL DEFAULT FALSE,
    auto_recharge_at    BIGINT NOT NULL DEFAULT 100,
    auto_recharge_amount BIGINT NOT NULL DEFAULT 1000,
    trial_ends_at       TIMESTAMPTZ,
    grace_ends_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_token_wallets_business_id ON token_wallets(business_id);
CREATE INDEX IF NOT EXISTS idx_token_wallets_balance ON token_wallets(balance);

-- ── etg_transactions (immutable ledger) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS etg_transactions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    wallet_id       UUID NOT NULL REFERENCES token_wallets(id) ON DELETE CASCADE,
    type            transaction_type NOT NULL,
    amount          BIGINT NOT NULL,
    balance_before  BIGINT NOT NULL,
    balance_after   BIGINT NOT NULL,
    description     TEXT,
    reference_id    TEXT,
    reference_type  TEXT,
    idempotency_key TEXT UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- immutable: no updated_at, no deleted_at, no ON DELETE CASCADE on FK
);

CREATE INDEX IF NOT EXISTS idx_etg_transactions_business_id ON etg_transactions(business_id);
CREATE INDEX IF NOT EXISTS idx_etg_transactions_wallet_id ON etg_transactions(wallet_id);
CREATE INDEX IF NOT EXISTS idx_etg_transactions_type ON etg_transactions(type);
CREATE INDEX IF NOT EXISTS idx_etg_transactions_created_at ON etg_transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_etg_transactions_idempotency_key ON etg_transactions(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Prevent any UPDATE or DELETE on the immutable ledger
CREATE OR REPLACE RULE etg_transactions_no_update AS
    ON UPDATE TO etg_transactions DO INSTEAD NOTHING;
CREATE OR REPLACE RULE etg_transactions_no_delete AS
    ON DELETE TO etg_transactions DO INSTEAD NOTHING;

-- ── usage_events ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS usage_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     TEXT NOT NULL,
    bot_id          TEXT,
    conversation_id TEXT,
    action_type     TEXT NOT NULL,
    model_id        TEXT,
    input_tokens    INT,
    output_tokens   INT,
    etg_charged     INT NOT NULL DEFAULT 0,
    latency_ms      INT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_events_business_id ON usage_events(business_id);
CREATE INDEX IF NOT EXISTS idx_usage_events_bot_id ON usage_events(bot_id) WHERE bot_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_usage_events_action_type ON usage_events(action_type);
CREATE INDEX IF NOT EXISTS idx_usage_events_created_at ON usage_events(created_at);
-- Composite for dashboard time-range queries
CREATE INDEX IF NOT EXISTS idx_usage_events_business_time
    ON usage_events(business_id, created_at DESC);

-- ── action_pricing ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS action_pricing (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    action_type TEXT NOT NULL UNIQUE,
    etg_cost    INT NOT NULL DEFAULT 1,
    description TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default pricing
INSERT INTO action_pricing (action_type, etg_cost, description) VALUES
    ('ai_reply',            2,  'Standard AI reply (Gemini Flash)'),
    ('ai_reply_premium',    5,  'Premium AI reply (GPT-4o / Claude)'),
    ('ai_reply_reasoning',  10, 'Reasoning model reply (o3)'),
    ('rag_search',          1,  'Knowledge base vector search'),
    ('ocr_extraction',      3,  'OCR on uploaded image/PDF'),
    ('image_generation',    10, 'Image generation'),
    ('order_created',       1,  'Order intake via bot'),
    ('calendar_booking',    2,  'Appointment booking')
ON CONFLICT (action_type) DO NOTHING;

-- ── escrow_records ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS escrow_records (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    unlock_id           UUID NOT NULL UNIQUE REFERENCES agent_unlocks(id) ON DELETE CASCADE,
    father_agent_id     UUID NOT NULL REFERENCES father_agents(id) ON DELETE CASCADE,
    creator_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    total_etg           INT NOT NULL,
    creator_share       INT NOT NULL,
    platform_share      INT NOT NULL,
    status              escrow_status NOT NULL DEFAULT 'holding',
    hold_until          TIMESTAMPTZ NOT NULL,
    released_at         TIMESTAMPTZ,
    disputed_at         TIMESTAMPTZ,
    dispute_reason      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_escrow_records_status ON escrow_records(status);
CREATE INDEX IF NOT EXISTS idx_escrow_records_hold_until ON escrow_records(hold_until)
    WHERE status = 'holding';
CREATE INDEX IF NOT EXISTS idx_escrow_records_creator_id ON escrow_records(creator_id);
CREATE INDEX IF NOT EXISTS idx_escrow_records_father_agent_id ON escrow_records(father_agent_id);

-- ── platform_settings ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed defaults
INSERT INTO platform_settings (key, value, description) VALUES
    ('etg_usd_rate',                '0.01',  'USD per 1 ETG'),
    ('etg_etb_rate',                '0.56',  'ETB per 1 ETG'),
    ('new_user_bonus_etg',          '100',   'ETG credited on signup'),
    ('referral_bonus_etg',          '200',   'ETG credited per successful referral'),
    ('max_bots_per_business',       '5',     'Hard cap on bots per business'),
    ('trial_duration_days',         '15',    'Agent trial duration in days'),
    ('escrow_release_days',         '7',     'Days before escrow auto-releases'),
    ('grace_period_hours',          '24',    'Grace period after zero balance'),
    ('creator_revenue_share',       '0.70',  'Creator share of unlock revenue'),
    ('balance_low_threshold',       '200',   'ETG — trigger low-balance alert'),
    ('balance_critical_threshold',  '50',    'ETG — trigger critical-balance alert')
ON CONFLICT (key) DO NOTHING;

COMMIT;
