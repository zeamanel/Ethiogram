-- migrations/020_bookings.sql
-- Native appointment records (Phase A booking engine). Bookings live in our own
-- table so the owner dashboard, reminders, and reschedule/cancel work WITHOUT
-- the business wiring Google Calendar. calendar_event_id is set only when a
-- booking is also mirrored to an external calendar. Idempotent.

CREATE TABLE IF NOT EXISTS bookings (
    id                   UUID NOT NULL,
    business_id          UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    conversation_id      UUID REFERENCES conversations(id),
    customer_platform_id VARCHAR(128) NOT NULL,
    customer_name        VARCHAR(255),
    customer_phone       VARCHAR(32),
    service_name         VARCHAR(255),
    starts_at            TIMESTAMP WITH TIME ZONE NOT NULL,
    ends_at              TIMESTAMP WITH TIME ZONE NOT NULL,
    status               VARCHAR(16) NOT NULL DEFAULT 'confirmed',
    price                VARCHAR(64),
    notes                TEXT,
    source               VARCHAR(16) NOT NULL DEFAULT 'telegram',
    calendar_event_id    VARCHAR(255),
    reminder_24h_sent_at TIMESTAMP WITH TIME ZONE,
    reminder_1h_sent_at  TIMESTAMP WITH TIME ZONE,
    created_at           TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at           TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_bookings_business_starts ON bookings (business_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_bookings_status_starts   ON bookings (status, starts_at);
CREATE INDEX IF NOT EXISTS ix_bookings_business_id      ON bookings (business_id);
CREATE INDEX IF NOT EXISTS ix_bookings_customer_platform_id ON bookings (customer_platform_id);
CREATE INDEX IF NOT EXISTS ix_bookings_starts_at        ON bookings (starts_at);
CREATE INDEX IF NOT EXISTS ix_bookings_status           ON bookings (status);
