-- 011_notification_dispatched_at.sql
-- The notification dispatch worker must track which notifications it has fully
-- handled. It previously filtered on is_read (the user's dashboard read flag,
-- which the worker never set) and re-selected the same rows forever.
--
-- Add a nullable dispatched_at terminal flag: the worker selects rows WHERE
-- dispatched_at IS NULL, appends each delivered channel to sent_via, and sets
-- dispatched_at once every intended channel is done. is_read stays the user's
-- read flag.
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dispatched_at timestamptz NULL;

-- Backfill: mark already-touched notifications as dispatched so the new filter
-- doesn't cause a one-time burst of re-sends for rows handled before this flag
-- existed.
-- Compare sent_via as text so this works whether the column is jsonb (ORM) or
-- a legacy text/json type — '[]'::jsonb has no <> operator against text.
UPDATE notifications
   SET dispatched_at = COALESCE(updated_at, now())
 WHERE dispatched_at IS NULL
   AND sent_via IS NOT NULL
   AND btrim(sent_via::text) NOT IN ('[]', '');

CREATE INDEX IF NOT EXISTS ix_notifications_dispatched_at
    ON notifications (dispatched_at);
