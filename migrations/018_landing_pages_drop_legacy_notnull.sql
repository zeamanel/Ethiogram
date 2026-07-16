-- migrations/018_landing_pages_drop_legacy_notnull.sql
-- landing_pages was first created by 005 with slug/title NOT NULL (no default),
-- but the ORM doesn't populate them (slug isn't an ORM column; title is
-- nullable in the ORM). So inserting a LandingPage via the website editor
-- violates those NOT NULLs. Align the DB with the ORM by dropping the NOT NULL
-- on the legacy columns wherever they still exist. Idempotent.

DO $$
DECLARE c text;
BEGIN
  FOREACH c IN ARRAY ARRAY['slug','title','sections'] LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'landing_pages' AND column_name = c AND is_nullable = 'NO'
    ) THEN
      EXECUTE format('ALTER TABLE landing_pages ALTER COLUMN %I DROP NOT NULL', c);
    END IF;
  END LOOP;
END $$;
