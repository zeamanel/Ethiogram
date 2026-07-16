-- migrations/015_child_agents_trials_reconcile.sql
-- Reconcile child_agents + agent_trials to the ORM. A missing column here makes
-- "Start free trial" fail with a 500 (INSERT references a column prod lacks).
-- Idempotent; safe to re-run.

-- child_agents (child_secrets came in 012, assigned_to_bot_id in 007)
ALTER TABLE child_agents ADD COLUMN IF NOT EXISTS display_name      VARCHAR(128);
ALTER TABLE child_agents ADD COLUMN IF NOT EXISTS child_data        JSONB;
ALTER TABLE child_agents ADD COLUMN IF NOT EXISTS child_secrets     TEXT;
ALTER TABLE child_agents ADD COLUMN IF NOT EXISTS is_active         BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE child_agents ADD COLUMN IF NOT EXISTS assigned_to_bot_id UUID;

-- agent_trials
ALTER TABLE agent_trials ADD COLUMN IF NOT EXISTS child_agent_id      UUID;
ALTER TABLE agent_trials ADD COLUMN IF NOT EXISTS is_converted        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agent_trials ADD COLUMN IF NOT EXISTS converted_at        TIMESTAMP WITH TIME ZONE;
ALTER TABLE agent_trials ADD COLUMN IF NOT EXISTS warning_sent_at     TIMESTAMP WITH TIME ZONE;
ALTER TABLE agent_trials ADD COLUMN IF NOT EXISTS critical_sent_at    TIMESTAMP WITH TIME ZONE;
ALTER TABLE agent_trials ADD COLUMN IF NOT EXISTS expired_notified_at TIMESTAMP WITH TIME ZONE;
