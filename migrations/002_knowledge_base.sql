-- migrations/002_knowledge_base.sql
-- Knowledge base: documents, chunks (pgvector), brain config, orders, products

BEGIN;

-- ── business_brain_config ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS business_brain_config (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id                 UUID NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,
    preferred_model_id          TEXT,
    system_prompt_override      TEXT,
    personality_tone            TEXT NOT NULL DEFAULT 'professional',
    response_language           TEXT NOT NULL DEFAULT 'auto',
    enable_rag                  BOOLEAN NOT NULL DEFAULT TRUE,
    rag_top_k                   INT NOT NULL DEFAULT 5,
    rag_similarity_threshold    NUMERIC(4,3) NOT NULL DEFAULT 0.75,
    max_history_messages        INT NOT NULL DEFAULT 20,
    enable_order_taking         BOOLEAN NOT NULL DEFAULT FALSE,
    enable_appointment_booking  BOOLEAN NOT NULL DEFAULT FALSE,
    enable_product_catalog      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brain_config_business_id ON business_brain_config(business_id);

-- ── knowledge_documents ───────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE doc_status AS ENUM ('pending', 'processing', 'ready', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE doc_type AS ENUM ('pdf', 'docx', 'xlsx', 'txt', 'csv', 'image', 'url', 'manual');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    doc_type        doc_type NOT NULL DEFAULT 'manual',
    status          doc_status NOT NULL DEFAULT 'pending',
    gcs_path        TEXT,
    source_url      TEXT,
    file_size_bytes BIGINT,
    mime_type       TEXT,
    chunk_count     INT NOT NULL DEFAULT 0,
    error_message   TEXT,
    processed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_docs_business_id ON knowledge_documents(business_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_docs_status ON knowledge_documents(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_docs_deleted_at ON knowledge_documents(deleted_at) WHERE deleted_at IS NULL;

-- ── knowledge_chunks ──────────────────────────────────────────────────────────
-- NOTE: embedding column added after table creation to use pgvector type
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content     TEXT NOT NULL,
    token_count INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add vector embedding column (768 dims for text-embedding-004)
ALTER TABLE knowledge_chunks
    ADD COLUMN IF NOT EXISTS embedding vector(768);

-- HNSW index for fast approximate nearest-neighbour cosine search
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document_id ON knowledge_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_business_id ON knowledge_chunks(business_id);

-- ── product_catalog ───────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE product_status AS ENUM ('active', 'inactive', 'out_of_stock');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS product_catalog (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    price           NUMERIC(12,2) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'ETB',
    sku             TEXT,
    stock_quantity  INT,
    image_url       TEXT,
    category        TEXT,
    status          product_status NOT NULL DEFAULT 'active',
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_product_catalog_business_id ON product_catalog(business_id);
CREATE INDEX IF NOT EXISTS idx_product_catalog_status ON product_catalog(status);
CREATE INDEX IF NOT EXISTS idx_product_catalog_deleted_at ON product_catalog(deleted_at) WHERE deleted_at IS NULL;

-- ── orders ────────────────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE order_status AS ENUM ('pending', 'confirmed', 'processing', 'completed', 'cancelled', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE payment_status AS ENUM ('unpaid', 'pending', 'paid', 'failed', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    bot_id          UUID REFERENCES bots(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    order_number    TEXT NOT NULL UNIQUE,
    customer_name   TEXT,
    customer_phone  TEXT,
    items           JSONB NOT NULL DEFAULT '[]',
    subtotal        NUMERIC(12,2) NOT NULL DEFAULT 0,
    tax             NUMERIC(12,2) NOT NULL DEFAULT 0,
    total           NUMERIC(12,2) NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'ETB',
    order_status    order_status NOT NULL DEFAULT 'pending',
    payment_status  payment_status NOT NULL DEFAULT 'unpaid',
    payment_method  TEXT,
    payment_ref     TEXT,
    notes           TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_business_id ON orders(business_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number);
CREATE INDEX IF NOT EXISTS idx_orders_order_status ON orders(order_status);
CREATE INDEX IF NOT EXISTS idx_orders_conversation_id ON orders(conversation_id);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);

COMMIT;
