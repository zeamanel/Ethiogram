-- 013_usage_events_uuid_realignment.sql
-- usage_events.business_id / bot_id / conversation_id are TEXT in the legacy
-- schema, but the ORM (app/db/models.py) declares them UUID. With the column as
-- text, comparing it to a UUID value ("WHERE bot_id = $1::uuid") raises
-- "operator does not exist: text = uuid" (the dashboard hit this). Convert the
-- columns to uuid so they match the ORM.
--
-- Existing values are stringified UUIDs (inserted via str(...)), so ::uuid casts
-- cleanly; NULLs (bot_id/conversation_id are nullable) cast to NULL. Guarded on
-- udt_name so re-running is a no-op (no needless table rewrite/lock).
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name='usage_events' AND column_name='business_id') <> 'uuid' THEN
    ALTER TABLE usage_events ALTER COLUMN business_id TYPE uuid USING business_id::uuid;
  END IF;
END $$;

DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name='usage_events' AND column_name='bot_id') <> 'uuid' THEN
    ALTER TABLE usage_events ALTER COLUMN bot_id TYPE uuid USING bot_id::uuid;
  END IF;
END $$;

DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name='usage_events' AND column_name='conversation_id') <> 'uuid' THEN
    ALTER TABLE usage_events ALTER COLUMN conversation_id TYPE uuid USING conversation_id::uuid;
  END IF;
END $$;
