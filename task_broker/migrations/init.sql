-- Task Broker Schema
-- AI Agent Economy — Task Contract Layer

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Task status enum ──────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE task_status AS ENUM (
        'pending',
        'assigned',
        'completed',
        'failed',
        'cancelled'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE attempt_status AS ENUM (
        'dispatched',
        'succeeded',
        'failed',
        'timed_out'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── Tasks ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capability_required VARCHAR(100) NOT NULL,
    input_payload       JSONB NOT NULL,
    status              task_status NOT NULL DEFAULT 'pending',
    result_payload      JSONB,                  -- NULL until completed
    error_message       TEXT,                   -- populated on failure
    submitted_by        VARCHAR(100) NOT NULL,  -- name/id of Agent A
    max_attempts        SMALLINT NOT NULL DEFAULT 3
                            CHECK (max_attempts BETWEEN 1 AND 10),
    timeout_seconds     SMALLINT NOT NULL DEFAULT 30
                            CHECK (timeout_seconds BETWEEN 5 AND 300),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Task attempts ─────────────────────────────────────────────────────────────
-- One row per agent tried. Provides full audit trail of which agents
-- were dispatched, in what order, and what each one returned.
CREATE TABLE IF NOT EXISTS task_attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL,              -- snapshot from registry at dispatch time
    agent_name      VARCHAR(100) NOT NULL,      -- snapshot — agent may be renamed later
    agent_endpoint  TEXT NOT NULL,              -- snapshot — endpoint may change later
    attempt_number  SMALLINT NOT NULL,          -- 1-based index within the task
    status          attempt_status NOT NULL DEFAULT 'dispatched',
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ                 -- NULL until attempt resolves
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks (status);

CREATE INDEX IF NOT EXISTS idx_tasks_submitted_by
    ON tasks (submitted_by);

CREATE INDEX IF NOT EXISTS idx_tasks_capability
    ON tasks (capability_required);

CREATE INDEX IF NOT EXISTS idx_task_attempts_task_id
    ON task_attempts (task_id);

-- ── updated_at trigger ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trigger_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();