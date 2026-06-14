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

\echo '==> 006_schema_alignment.sql'
\i migrations/006_schema_alignment.sql

\echo '==> All migrations applied successfully.'
