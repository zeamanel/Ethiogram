-- migrations/001_users_auth.sql
-- Users, authentication, sessions, creator profiles, businesses, bots

BEGIN;

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ── Enums ─────────────────────────────────────────────────────────────────────
CREATE TYPE user_role AS ENUM ('user', 'creator', 'admin', 'super_admin');
CREATE TYPE bot_status AS ENUM ('active', 'paused', 'disconnected', 'suspended');
CREATE TYPE subscription_plan AS ENUM ('free', 'starter', 'growth', 'enterprise');

-- ── users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email               TEXT UNIQUE,
    hashed_password     TEXT,
    full_name           TEXT,
    telegram_id         BIGINT UNIQUE,
    telegram_username   TEXT,
    telegram_photo_url  TEXT,
    role                user_role NOT NULL DEFAULT 'user',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    is_verified         BOOLEAN NOT NULL DEFAULT FALSE,
    referral_code       TEXT UNIQUE,
    referred_by_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    preferred_language  TEXT NOT NULL DEFAULT 'en',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id) WHERE telegram_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users(deleted_at) WHERE deleted_at IS NULL;

-- ── user_sessions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token   TEXT NOT NULL UNIQUE,
    device_info     TEXT,
    ip_address      TEXT,
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_refresh_token ON user_sessions(refresh_token);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at ON user_sessions(expires_at);

-- ── creator_profiles ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS creator_profiles (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    display_name            TEXT NOT NULL,
    bio                     TEXT,
    avatar_url              TEXT,
    portfolio_url           TEXT,
    total_agents_published  INT NOT NULL DEFAULT 0,
    total_unlocks           INT NOT NULL DEFAULT 0,
    total_etg_earned        BIGINT NOT NULL DEFAULT 0,
    avg_rating              NUMERIC(3,2) NOT NULL DEFAULT 0,
    rating_count            INT NOT NULL DEFAULT 0,
    is_verified_creator     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_creator_profiles_user_id ON creator_profiles(user_id);

-- ── businesses ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS businesses (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    description     TEXT,
    logo_url        TEXT,
    website_url     TEXT,
    industry        TEXT,
    country         TEXT NOT NULL DEFAULT 'ET',
    phone           TEXT,
    is_suspended    BOOLEAN NOT NULL DEFAULT FALSE,
    suspended_at    TIMESTAMPTZ,
    suspended_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_businesses_owner_id ON businesses(owner_id);
CREATE INDEX IF NOT EXISTS idx_businesses_slug ON businesses(slug);
CREATE INDEX IF NOT EXISTS idx_businesses_deleted_at ON businesses(deleted_at) WHERE deleted_at IS NULL;

-- ── bots ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bots (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id         UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    bot_token_encrypted TEXT NOT NULL,
    bot_token_hash      TEXT NOT NULL UNIQUE,
    bot_username        TEXT,
    bot_name            TEXT,
    status              bot_status NOT NULL DEFAULT 'active',
    webhook_url         TEXT,
    webhook_set_at      TIMESTAMPTZ,
    last_active_at      TIMESTAMPTZ,
    total_messages      BIGINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bots_business_id ON bots(business_id);
CREATE INDEX IF NOT EXISTS idx_bots_token_hash ON bots(bot_token_hash);
CREATE INDEX IF NOT EXISTS idx_bots_status ON bots(status);
CREATE INDEX IF NOT EXISTS idx_bots_deleted_at ON bots(deleted_at) WHERE deleted_at IS NULL;

-- ── conversations ─────────────────────────────────────────────────────────────
CREATE TYPE platform_type AS ENUM ('telegram', 'whatsapp', 'instagram', 'web');

CREATE TABLE IF NOT EXISTS conversations (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id             UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    bot_id                  UUID REFERENCES bots(id) ON DELETE SET NULL,
    platform                platform_type NOT NULL DEFAULT 'telegram',
    customer_platform_id    TEXT NOT NULL,
    customer_name           TEXT,
    customer_username       TEXT,
    detected_language       TEXT NOT NULL DEFAULT 'en',
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    total_messages          INT NOT NULL DEFAULT 0,
    total_etg_spent         BIGINT NOT NULL DEFAULT 0,
    last_message_at         TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_unique
    ON conversations(business_id, platform, customer_platform_id);
CREATE INDEX IF NOT EXISTS idx_conversations_business_id ON conversations(business_id);
CREATE INDEX IF NOT EXISTS idx_conversations_bot_id ON conversations(bot_id);
CREATE INDEX IF NOT EXISTS idx_conversations_last_message_at ON conversations(last_message_at);
CREATE INDEX IF NOT EXISTS idx_conversations_customer_platform_id ON conversations(customer_platform_id);

-- ── chat_messages ─────────────────────────────────────────────────────────────
CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system', 'tool');
CREATE TYPE media_type AS ENUM ('text', 'image', 'audio', 'video', 'document', 'location', 'sticker');

CREATE TABLE IF NOT EXISTS chat_messages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            message_role NOT NULL,
    content         TEXT NOT NULL,
    media_type      media_type NOT NULL DEFAULT 'text',
    media_url       TEXT,
    model_id        TEXT,
    input_tokens    INT,
    output_tokens   INT,
    latency_ms      INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_id ON chat_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at);

-- ── admin_audit_log ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    action          TEXT NOT NULL,
    target_type     TEXT,
    target_id       TEXT,
    details         JSONB,
    ip_address      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin_id ON admin_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created_at ON admin_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_action ON admin_audit_log(action);

-- ── notifications ─────────────────────────────────────────────────────────────
CREATE TYPE notification_type AS ENUM (
    'balance_low', 'balance_critical', 'balance_zero',
    'trial_warning', 'trial_expired',
    'agent_approved', 'agent_rejected',
    'order_received', 'order_paid', 'escrow_released',
    'broadcast', 'system'
);

CREATE TABLE IF NOT EXISTS notifications (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type        notification_type NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    data        JSONB,
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    sent_via    TEXT,
    sent_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at);

COMMIT;
