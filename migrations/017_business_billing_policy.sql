-- migrations/017_business_billing_policy.sql
-- Business billing settings: who pays per message, per-user free-tier cap, and
-- user-pays pricing (service price + business markup). Idempotent.
-- Existing businesses default to business_pays with no per-user limit
-- (backward-compatible — unchanged behaviour).

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS billing_policy          VARCHAR(16) NOT NULL DEFAULT 'business_pays';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS per_user_monthly_limit  INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS per_user_limit_action   VARCHAR(16) NOT NULL DEFAULT 'block';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS service_price           INTEGER NOT NULL DEFAULT 0;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS business_markup         INTEGER NOT NULL DEFAULT 0;

-- Per-business end-user ledger (user_pays / free-tier cap).
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS etg_balance      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS monthly_etg_used INTEGER NOT NULL DEFAULT 0;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS monthly_reset_at TIMESTAMP WITH TIME ZONE;

-- Global end-user wallet (reserved for the recharge phase).
ALTER TABLE users ADD COLUMN IF NOT EXISTS etg_balance INTEGER NOT NULL DEFAULT 0;

-- Records who paid for each metered message.
ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS payer VARCHAR(16);
