-- 010_chat_messages_media_type_to_text.sql
-- chat_messages.media_type is an ENUM ('media_type') in the legacy schema, but
-- the ORM maps the attribute as a plain string (mapped_column(String(...))).
-- SQLAlchemy therefore binds the parameter as ::VARCHAR, which Postgres rejects
-- against an enum column ("column media_type is of type media_type but
-- expression is of type character varying") — even when the value is NULL,
-- because the mismatch is detected at plan time. Convert the column to text so
-- it matches what the ORM emits.
ALTER TABLE chat_messages ALTER COLUMN media_type DROP DEFAULT;
ALTER TABLE chat_messages ALTER COLUMN media_type TYPE text USING media_type::text;
