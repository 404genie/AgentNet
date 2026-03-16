-- Payment Railway Schema
-- AI Agent Economy — Credit Ledger

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Hold status enum ──────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE hold_status AS ENUM ('active', 'settled', 'released');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── Transaction type enum ─────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE tx_type AS ENUM ('topup', 'hold', 'release', 'settlement');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── Wallets ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wallets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL UNIQUE,
    agent_name      VARCHAR(100) NOT NULL,
    balance         NUMERIC(12, 2) NOT NULL DEFAULT 0.00
                        CHECK (balance >= 0.00),
    held_balance    NUMERIC(12, 2) NOT NULL DEFAULT 0.00
                        CHECK (held_balance >= 0.00),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- held_balance can never exceed total balance
    CONSTRAINT held_lte_balance CHECK (held_balance <= balance)
);

-- ── Holds ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS holds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_id       UUID NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    task_id         UUID NOT NULL UNIQUE,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    status          hold_status NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Transactions ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent_id   UUID REFERENCES wallets(agent_id) ON DELETE SET NULL,
    to_agent_id     UUID NOT NULL REFERENCES wallets(agent_id) ON DELETE CASCADE,
    task_id         UUID,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    tx_type         tx_type NOT NULL,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_wallets_agent_id
    ON wallets (agent_id);

CREATE INDEX IF NOT EXISTS idx_holds_task_id
    ON holds (task_id);

CREATE INDEX IF NOT EXISTS idx_holds_wallet_id
    ON holds (wallet_id);

CREATE INDEX IF NOT EXISTS idx_holds_status
    ON holds (status);

CREATE INDEX IF NOT EXISTS idx_transactions_from_agent
    ON transactions (from_agent_id);

CREATE INDEX IF NOT EXISTS idx_transactions_to_agent
    ON transactions (to_agent_id);

CREATE INDEX IF NOT EXISTS idx_transactions_task_id
    ON transactions (task_id);

-- ── updated_at triggers ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trigger_wallets_updated_at
    BEFORE UPDATE ON wallets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE TRIGGER trigger_holds_updated_at
    BEFORE UPDATE ON holds
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();