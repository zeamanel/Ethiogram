-- 009_relax_legacy_notnull.sql
-- The legacy etg_transactions table (from migrations 001-005) carries columns
-- the ORM never populates, both NOT NULL, causing NotNullViolation on insert
-- in the webhook billing path (after the reply is already sent -> 500 -> Telegram
-- retries -> duplicate/lagging replies):
--   * business_id : the ORM links a transaction via wallet_id, not business_id
--   * type        : the ORM writes the newer 'transaction_type' column instead
-- Relax both. DROP NOT NULL is idempotent (no error if already nullable).
ALTER TABLE etg_transactions ALTER COLUMN business_id DROP NOT NULL;
ALTER TABLE etg_transactions ALTER COLUMN type DROP NOT NULL;
