-- migrations/021_business_transfers.sql
-- Pending business ownership transfers (My Bots → Transfer business). Ownership
-- only moves on recipient acceptance; rows are identified by a Telegram username,
-- a Telegram id, or an email until the matching user claims them. Idempotent.

CREATE TABLE IF NOT EXISTS business_transfers (
    id             UUID NOT NULL,
    business_id    UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    from_user_id   UUID NOT NULL REFERENCES users(id),
    to_kind        VARCHAR(16) NOT NULL,
    to_value       VARCHAR(255) NOT NULL,
    to_user_id     UUID REFERENCES users(id),
    status         VARCHAR(16) NOT NULL DEFAULT 'pending',
    token          VARCHAR(64) NOT NULL,
    expires_at     TIMESTAMP WITH TIME ZONE NOT NULL,
    resolved_at    TIMESTAMP WITH TIME ZONE,
    resolved_by_id UUID REFERENCES users(id),
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_transfers_token ON business_transfers (token);
CREATE INDEX IF NOT EXISTS ix_business_transfers_business_id ON business_transfers (business_id);
CREATE INDEX IF NOT EXISTS ix_business_transfers_to_value ON business_transfers (to_value);
CREATE INDEX IF NOT EXISTS ix_business_transfers_status ON business_transfers (status);
