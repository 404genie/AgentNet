-- Reputation Protocol Schema
-- AI Agent Economy — Reputation Scoring Layer

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Outcome enum ──────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE task_outcome AS ENUM ('completed', 'failed', 'timed_out');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── Reputation scores ─────────────────────────────────────────────────────────
-- One row per agent — updated in-place on every event.
CREATE TABLE IF NOT EXISTS reputation_scores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            UUID NOT NULL UNIQUE,
    agent_name          VARCHAR(100) NOT NULL,

    -- Raw counters
    total_tasks         INTEGER NOT NULL DEFAULT 0 CHECK (total_tasks >= 0),
    successful_tasks    INTEGER NOT NULL DEFAULT 0 CHECK (successful_tasks >= 0),
    failed_tasks        INTEGER NOT NULL DEFAULT 0 CHECK (failed_tasks >= 0),
    timed_out_tasks     INTEGER NOT NULL DEFAULT 0 CHECK (timed_out_tasks >= 0),
    total_response_ms   BIGINT  NOT NULL DEFAULT 0 CHECK (total_response_ms >= 0),
    successful_payments INTEGER NOT NULL DEFAULT 0 CHECK (successful_payments >= 0),

    -- Score components (0.0000–1.0000)
    success_rate        NUMERIC(5, 4) NOT NULL DEFAULT 0.0000,
    reliability_score   NUMERIC(5, 4) NOT NULL DEFAULT 0.0000,
    time_score          NUMERIC(5, 4) NOT NULL DEFAULT 0.0000,
    payment_score       NUMERIC(5, 4) NOT NULL DEFAULT 0.0000,

    -- Final scores
    reputation_score    NUMERIC(5, 2) NOT NULL DEFAULT 0.00
                            CHECK (reputation_score >= 0.00 AND reputation_score <= 100.00),
    registry_score      NUMERIC(3, 2) NOT NULL DEFAULT 0.00
                            CHECK (registry_score >= 0.00 AND registry_score <= 5.00),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Counter consistency: parts can't exceed total
    CONSTRAINT tasks_sum_check
        CHECK (successful_tasks + failed_tasks + timed_out_tasks <= total_tasks),
    CONSTRAINT payments_lte_successful
        CHECK (successful_payments <= successful_tasks)
);

-- ── Reputation events ─────────────────────────────────────────────────────────
-- Immutable audit log — one row per task outcome.
CREATE TABLE IF NOT EXISTS reputation_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            UUID NOT NULL REFERENCES reputation_scores(agent_id) ON DELETE CASCADE,
    task_id             UUID NOT NULL UNIQUE,   -- one event per task, enforced at DB level
    outcome             task_outcome NOT NULL,
    response_ms         BIGINT CHECK (response_ms >= 0),  -- NULL for failed/timed_out
    payment_successful  BOOLEAN NOT NULL DEFAULT FALSE,
    score_before        NUMERIC(5, 2) NOT NULL,
    score_after         NUMERIC(5, 2) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_reputation_scores_agent_id
    ON reputation_scores (agent_id);

CREATE INDEX IF NOT EXISTS idx_reputation_scores_score
    ON reputation_scores (reputation_score DESC);

CREATE INDEX IF NOT EXISTS idx_reputation_events_agent_id
    ON reputation_events (agent_id);

CREATE INDEX IF NOT EXISTS idx_reputation_events_task_id
    ON reputation_events (task_id);

CREATE INDEX IF NOT EXISTS idx_reputation_events_created_at
    ON reputation_events (created_at DESC);

-- ── updated_at trigger ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trigger_reputation_scores_updated_at
    BEFORE UPDATE ON reputation_scores
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();