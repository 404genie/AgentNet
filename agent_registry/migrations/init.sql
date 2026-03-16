-- Agent Registry Schema
-- AI Agent Economy - Discovery Protocol

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS agents (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             VARCHAR(100) NOT NULL UNIQUE,
    endpoint         TEXT NOT NULL,
    capabilities     TEXT[] NOT NULL DEFAULT '{}',
    price            NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    reputation       NUMERIC(3, 2) NOT NULL DEFAULT 0.00
                         CHECK (reputation >= 0.00 AND reputation <= 5.00),
    agent_version    VARCHAR(50) NOT NULL DEFAULT '1.0.0',
    capability_schema JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup by capability tag (GIN index on array column)
CREATE INDEX IF NOT EXISTS idx_agents_capabilities
    ON agents USING GIN (capabilities);

-- Fast sorting by reputation and price
CREATE INDEX IF NOT EXISTS idx_agents_reputation ON agents (reputation DESC);
CREATE INDEX IF NOT EXISTS idx_agents_price ON agents (price ASC);

-- GIN index on capability_schema for fast JSONB key lookups
CREATE INDEX IF NOT EXISTS idx_agents_capability_schema
    ON agents USING GIN (capability_schema);

-- Auto-update updated_at on row change
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trigger_agents_updated_at
    BEFORE UPDATE ON agents
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();