-- 008_enum_type_realignment.sql
-- Realigns enum COLUMN TYPES on active code tables to the names the ORM emits.
-- The old migrations (001-005) created enums with names like 'platform_type',
-- 'bot_status', 'message_role', 'user_role', etc., but SQLAlchemy casts to the
-- bare class-name-lowercased types ('platform', 'botstatus', 'messagerole',
-- 'userrole', …) created in 006. Mismatched names cause asyncpg
-- DatatypeMismatchError on INSERT (e.g. "column is of type message_role but
-- expression is of type messagerole").
--
-- Pattern per column: drop any default (old-typed default can't auto-cast),
-- convert the type through ::text::<ormtype>, since the values are identical.
-- The application supplies these values on every insert, so no DB default is
-- re-added. Safe to re-run.

-- chat_messages.role : message_role -> messagerole  (fires right after each reply)
ALTER TABLE chat_messages ALTER COLUMN role DROP DEFAULT;
ALTER TABLE chat_messages ALTER COLUMN role TYPE messagerole USING role::text::messagerole;

-- bots.platform : platform_type -> platform
ALTER TABLE bots ALTER COLUMN platform DROP DEFAULT;
ALTER TABLE bots ALTER COLUMN platform TYPE platform USING platform::text::platform;

-- bots.status : bot_status -> botstatus
ALTER TABLE bots ALTER COLUMN status DROP DEFAULT;
ALTER TABLE bots ALTER COLUMN status TYPE botstatus USING status::text::botstatus;

-- users.role : user_role -> userrole
ALTER TABLE users ALTER COLUMN role DROP DEFAULT;
ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole;

-- knowledge_documents.status : doc_status -> documentstatus
ALTER TABLE knowledge_documents ALTER COLUMN status DROP DEFAULT;
ALTER TABLE knowledge_documents ALTER COLUMN status TYPE documentstatus USING status::text::documentstatus;

-- orders.order_status : order_status -> orderstatus
ALTER TABLE orders ALTER COLUMN order_status DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN order_status TYPE orderstatus USING order_status::text::orderstatus;

-- orders.payment_status : payment_status -> paymentstatus
ALTER TABLE orders ALTER COLUMN payment_status DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN payment_status TYPE paymentstatus USING payment_status::text::paymentstatus;

-- token_wallets.subscription_plan : subscription_plan -> subscriptionplan
ALTER TABLE token_wallets ALTER COLUMN subscription_plan DROP DEFAULT;
ALTER TABLE token_wallets ALTER COLUMN subscription_plan TYPE subscriptionplan USING subscription_plan::text::subscriptionplan;
