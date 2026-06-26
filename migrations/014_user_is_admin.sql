-- migrations/014_user_is_admin.sql
-- Adds users.is_admin for the Mini App admin dashboard (admin gate is
-- is_admin OR role='admin'). Idempotent; safe to re-run.

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Keep existing role='admin' users consistent with the new flag.
UPDATE users SET is_admin = TRUE WHERE role = 'admin' AND is_admin = FALSE;
