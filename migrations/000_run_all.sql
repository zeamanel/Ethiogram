-- migrations/000_run_all.sql
-- Run all migrations in order. Safe to run multiple times (idempotent).
--
-- Usage:
--   psql $DATABASE_URL -f migrations/000_run_all.sql
--   docker exec -i <postgres-container> psql -U postgres ethiogram < migrations/000_run_all.sql

\echo '==> 001_users_auth.sql'
\i migrations/001_users_auth.sql

\echo '==> 002_knowledge_base.sql'
\i migrations/002_knowledge_base.sql

\echo '==> 003_agents_marketplace.sql'
\i migrations/003_agents_marketplace.sql

\echo '==> 004_token_economy.sql'
\i migrations/004_token_economy.sql

\echo '==> 005_mini_app_landing_mcp.sql'
\i migrations/005_mini_app_landing_mcp.sql

-- 006-011: ORM reconciliation + later fixes. All idempotent (006/007 use
-- IF NOT EXISTS, 008/010 are type-guarded, 009 uses DROP NOT NULL, 011 uses
-- ADD COLUMN IF NOT EXISTS). On a fresh DB these complete the schema to match
-- app/db/models.py; on an up-to-date DB they are no-ops.
\echo '==> 006_orm_reconciliation.sql'
\i migrations/006_orm_reconciliation.sql

\echo '==> 007_column_reconciliation.sql'
\i migrations/007_column_reconciliation.sql

\echo '==> 008_enum_type_realignment.sql'
\i migrations/008_enum_type_realignment.sql

\echo '==> 009_relax_legacy_notnull.sql'
\i migrations/009_relax_legacy_notnull.sql

\echo '==> 010_chat_messages_media_type_to_text.sql'
\i migrations/010_chat_messages_media_type_to_text.sql

\echo '==> 011_notification_dispatched_at.sql'
\i migrations/011_notification_dispatched_at.sql

\echo '==> 012_child_agent_secrets.sql'
\i migrations/012_child_agent_secrets.sql

\echo '==> 013_usage_events_uuid_realignment.sql'
\i migrations/013_usage_events_uuid_realignment.sql

\echo '==> 014_user_is_admin.sql'
\i migrations/014_user_is_admin.sql

\echo '==> 015_child_agents_trials_reconcile.sql'
\i migrations/015_child_agents_trials_reconcile.sql

\echo '==> 016_drop_legacy_father_agent_id_notnull.sql'
\i migrations/016_drop_legacy_father_agent_id_notnull.sql

\echo '==> 017_business_billing_policy.sql'
\i migrations/017_business_billing_policy.sql

\echo '==> 018_landing_pages_drop_legacy_notnull.sql'
\i migrations/018_landing_pages_drop_legacy_notnull.sql

\echo '==> 019_mini_app_fonts.sql'
\i migrations/019_mini_app_fonts.sql

\echo '==> All migrations applied successfully.'
