-- migrations/006_schema_alignment.sql
-- Align the database schema with the ORM models.
--
-- The original migrations were written with different column names and missing
-- enum values compared to the ORM. This migration is idempotent — safe to
-- run multiple times.

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Fix enums
-- ─────────────────────────────────────────────────────────────────────────────

-- user_role: add 'owner' and 'support' (ORM has owner/creator/admin/support)
ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'owner';
ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'support';

-- bot_status: add 'grace' (ORM has active/paused/grace/disconnected/suspended)
ALTER TYPE bot_status ADD VALUE IF NOT EXISTS 'grace';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Fix users table
-- ─────────────────────────────────────────────────────────────────────────────
-- telegram_username -> username
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='users' AND column_name='telegram_username') THEN
        ALTER TABLE users RENAME COLUMN telegram_username TO username;
    END IF;
END $$;

-- telegram_photo_url -> avatar_url
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='users' AND column_name='telegram_photo_url') THEN
        ALTER TABLE users RENAME COLUMN telegram_photo_url TO avatar_url;
    END IF;
END $$;

-- preferred_language -> language_code
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='users' AND column_name='preferred_language') THEN
        ALTER TABLE users RENAME COLUMN preferred_language TO language_code;
    END IF;
END $$;

ALTER TABLE users ADD COLUMN IF NOT EXISTS phone         VARCHAR(32)   UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS suspended_at  TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS suspension_reason TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url    VARCHAR(512);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Fix user_sessions table
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS access_token_jti  VARCHAR(64);
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS refresh_token_jti VARCHAR(64);
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS revoked_at        TIMESTAMPTZ;
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS user_agent        VARCHAR(512);
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS platform          VARCHAR(32) NOT NULL DEFAULT 'web';
ALTER TABLE user_sessions ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Sync legacy is_revoked -> revoked_at
UPDATE user_sessions
   SET revoked_at = NOW()
 WHERE is_revoked = TRUE AND revoked_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Fix businesses table
-- ─────────────────────────────────────────────────────────────────────────────
-- country -> country_code
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='businesses' AND column_name='country') THEN
        ALTER TABLE businesses RENAME COLUMN country TO country_code;
    END IF;
END $$;

-- industry -> category (ORM uses 'category', migration had 'industry')
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='businesses' AND column_name='industry') THEN
        ALTER TABLE businesses RENAME COLUMN industry TO category;
    END IF;
END $$;

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS category             VARCHAR(64);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS brand_primary_color  VARCHAR(7)  NOT NULL DEFAULT '#1a73e8';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS brand_secondary_color VARCHAR(7) NOT NULL DEFAULT '#ffffff';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS country_code         VARCHAR(2)  NOT NULL DEFAULT 'ET';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS timezone             VARCHAR(64) NOT NULL DEFAULT 'Africa/Addis_Ababa';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS currency             VARCHAR(3)  NOT NULL DEFAULT 'ETB';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS languages            JSONB       NOT NULL DEFAULT '["am","en"]';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS latitude             FLOAT;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS longitude            FLOAT;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS address              TEXT;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email                VARCHAR(255);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS mcp_enabled          BOOLEAN     NOT NULL DEFAULT TRUE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS suspended_reason     TEXT;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Fix bots table
-- ─────────────────────────────────────────────────────────────────────────────
-- bot_token_encrypted -> encrypted_token
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='bots' AND column_name='bot_token_encrypted') THEN
        ALTER TABLE bots RENAME COLUMN bot_token_encrypted TO encrypted_token;
    END IF;
END $$;

-- bot_token_hash -> token_hash
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='bots' AND column_name='bot_token_hash') THEN
        ALTER TABLE bots RENAME COLUMN bot_token_hash TO token_hash;
    END IF;
END $$;

-- bot_name -> bot_display_name
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='bots' AND column_name='bot_name') THEN
        ALTER TABLE bots RENAME COLUMN bot_name TO bot_display_name;
    END IF;
END $$;

-- last_active_at -> last_message_at
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='bots' AND column_name='last_active_at') THEN
        ALTER TABLE bots RENAME COLUMN last_active_at TO last_message_at;
    END IF;
END $$;

-- total_messages -> total_messages_processed
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='bots' AND column_name='total_messages') THEN
        ALTER TABLE bots RENAME COLUMN total_messages TO total_messages_processed;
    END IF;
END $$;

ALTER TABLE bots ADD COLUMN IF NOT EXISTS total_etg_consumed       BIGINT      NOT NULL DEFAULT 0;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS grace_period_started_at  TIMESTAMPTZ;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS suspended_at             TIMESTAMPTZ;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS suspension_reason        TEXT;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS webhook_secret           VARCHAR(128);
ALTER TABLE bots ADD COLUMN IF NOT EXISTS platform                 platform_type NOT NULL DEFAULT 'telegram';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Fix notifications table
-- ─────────────────────────────────────────────────────────────────────────────
-- type -> notification_type (ORM column is 'notification_type')
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='notifications' AND column_name='type') THEN
        ALTER TABLE notifications RENAME COLUMN type TO notification_type;
    END IF;
END $$;

-- Change notification_type column to plain TEXT (ORM uses String, not the enum)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='notifications' AND column_name='notification_type'
          AND data_type = 'USER-DEFINED'
    ) THEN
        ALTER TABLE notifications
            ALTER COLUMN notification_type TYPE TEXT
            USING notification_type::TEXT;
    END IF;
END $$;

-- sent_via: change from TEXT to JSONB (ORM stores a list)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='notifications' AND column_name='sent_via'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE notifications ALTER COLUMN sent_via DROP DEFAULT;
        ALTER TABLE notifications
            ALTER COLUMN sent_via TYPE JSONB
            USING CASE WHEN sent_via IS NULL THEN '[]'::jsonb
                       ELSE to_jsonb(sent_via) END;
        ALTER TABLE notifications ALTER COLUMN sent_via SET NOT NULL;
        ALTER TABLE notifications ALTER COLUMN sent_via SET DEFAULT '[]';
    END IF;
END $$;

ALTER TABLE notifications ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ;

COMMIT;
