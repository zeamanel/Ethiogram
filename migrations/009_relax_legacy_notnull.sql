-- 009_relax_legacy_notnull.sql
-- The legacy etg_transactions table (from migrations 001-005) carries columns
-- the ORM never populates, both NOT NULL, causing NotNullViolation on insert
-- in the webhook billing path (after the reply is already sent -> 500 -> Telegram
-- retries -> duplicate/lagging replies):
--   * business_id : the ORM links a transaction via wallet_id, not business_id
--   * type        : the ORM writes the newer 'transaction_type' column instead
-- Relax both. Guarded on column existence so this is a no-op (not an error) on a
-- DB that never had these legacy columns; DROP NOT NULL is itself idempotent.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'etg_transactions' AND column_name = 'business_id') THEN
    ALTER TABLE etg_transactions ALTER COLUMN business_id DROP NOT NULL;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'etg_transactions' AND column_name = 'type') THEN
    ALTER TABLE etg_transactions ALTER COLUMN type DROP NOT NULL;
  END IF;
END $$;
