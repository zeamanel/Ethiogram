-- migrations/016_drop_legacy_father_agent_id_notnull.sql
-- Legacy father_agent_id (from 003) is NOT NULL but the ORM uses agent_id and
-- never populates it — so any ORM INSERT into these tables fails with a 500
-- (this is what broke "Start free trial"; the same lurked in unlock/review).
-- Drop the NOT NULL wherever the column still exists. Idempotent.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['child_agents','agent_unlocks','agent_trials','agent_reviews'] LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = t AND column_name = 'father_agent_id' AND is_nullable = 'NO'
    ) THEN
      EXECUTE format('ALTER TABLE %I ALTER COLUMN father_agent_id DROP NOT NULL', t);
    END IF;
  END LOOP;
END $$;
