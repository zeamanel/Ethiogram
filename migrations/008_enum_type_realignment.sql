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
-- re-added.
--
-- IDEMPOTENCY: `ALTER COLUMN ... TYPE ... USING` ALWAYS rewrites the table and
-- takes an ACCESS EXCLUSIVE lock, even when converting a type to itself — so a
-- bare re-run would needlessly rewrite/lock these tables in prod. Each block is
-- therefore guarded on information_schema.udt_name and only converts when the
-- column is not ALREADY the target type. On a fresh DB the column still has the
-- legacy type, so the guard fires; on an up-to-date DB it is a true no-op.

-- chat_messages.role : message_role -> messagerole  (fires right after each reply)
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'chat_messages' AND column_name = 'role') <> 'messagerole' THEN
    ALTER TABLE chat_messages ALTER COLUMN role DROP DEFAULT;
    ALTER TABLE chat_messages ALTER COLUMN role TYPE messagerole USING role::text::messagerole;
  END IF;
END $$;

-- bots.platform : platform_type -> platform
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'bots' AND column_name = 'platform') <> 'platform' THEN
    ALTER TABLE bots ALTER COLUMN platform DROP DEFAULT;
    ALTER TABLE bots ALTER COLUMN platform TYPE platform USING platform::text::platform;
  END IF;
END $$;

-- bots.status : bot_status -> botstatus
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'bots' AND column_name = 'status') <> 'botstatus' THEN
    ALTER TABLE bots ALTER COLUMN status DROP DEFAULT;
    ALTER TABLE bots ALTER COLUMN status TYPE botstatus USING status::text::botstatus;
  END IF;
END $$;

-- users.role : user_role -> userrole
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'role') <> 'userrole' THEN
    ALTER TABLE users ALTER COLUMN role DROP DEFAULT;
    ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole;
  END IF;
END $$;

-- knowledge_documents.status : doc_status -> documentstatus
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'knowledge_documents' AND column_name = 'status') <> 'documentstatus' THEN
    ALTER TABLE knowledge_documents ALTER COLUMN status DROP DEFAULT;
    ALTER TABLE knowledge_documents ALTER COLUMN status TYPE documentstatus USING status::text::documentstatus;
  END IF;
END $$;

-- orders.order_status : order_status -> orderstatus
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'order_status') <> 'orderstatus' THEN
    ALTER TABLE orders ALTER COLUMN order_status DROP DEFAULT;
    ALTER TABLE orders ALTER COLUMN order_status TYPE orderstatus USING order_status::text::orderstatus;
  END IF;
END $$;

-- orders.payment_status : payment_status -> paymentstatus
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'orders' AND column_name = 'payment_status') <> 'paymentstatus' THEN
    ALTER TABLE orders ALTER COLUMN payment_status DROP DEFAULT;
    ALTER TABLE orders ALTER COLUMN payment_status TYPE paymentstatus USING payment_status::text::paymentstatus;
  END IF;
END $$;

-- token_wallets.subscription_plan : subscription_plan -> subscriptionplan
DO $$ BEGIN
  IF (SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'token_wallets' AND column_name = 'subscription_plan') <> 'subscriptionplan' THEN
    ALTER TABLE token_wallets ALTER COLUMN subscription_plan DROP DEFAULT;
    ALTER TABLE token_wallets ALTER COLUMN subscription_plan TYPE subscriptionplan USING subscription_plan::text::subscriptionplan;
  END IF;
END $$;
