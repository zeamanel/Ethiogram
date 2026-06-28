-- migrations/019_mini_app_fonts.sql
-- Dedicated heading/body font columns for the storefront (the editor's font
-- selectors). Existing rows are NULL and fall back to the storefront defaults
-- (Sora / DM Sans). ui_child_prompt already exists (006/007). Idempotent.

ALTER TABLE mini_app_configs ADD COLUMN IF NOT EXISTS font_heading VARCHAR(64);
ALTER TABLE mini_app_configs ADD COLUMN IF NOT EXISTS font_body    VARCHAR(64);
