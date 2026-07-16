-- 012_child_agent_secrets.sql
-- Split sensitive ChildAgent config out of the plaintext child_data JSONB into
-- an encrypted column. Calendar/API credentials must never sit in plaintext
-- JSONB (and must never be rendered into the LLM prompt). child_secrets holds a
-- Fernet-encrypted JSON blob (same pattern as encrypted bot tokens / API keys),
-- decrypted only in code and exposed to the agent under the "_secrets" key.
ALTER TABLE child_agents ADD COLUMN IF NOT EXISTS child_secrets text NULL;
