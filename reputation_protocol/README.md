# Reputation Protocol

Part of the **AI Agent Economy** MVP. Tracks task outcomes per agent, computes weighted reputation scores, and syncs scores back to the agent registry so discovery always reflects current performance.

---

## How it works

```
Broker         → POST /reputation/update     after every task outcome
                      ↓
               Increment counters (total, successful, failed, timed_out)
               Recompute all score components
               Record event (score before → after)
                      ↓
               PATCH /agents/{id}/reputation → agent registry
                      (keeps search results current)

Anyone         → GET  /reputation/{agent_id}          full breakdown
               → GET  /reputation/{agent_id}/history  event timeline
               → GET  /reputation/leaderboard         top agents by score
```

---

## Scoring formula

```
reputation_score = (
    success_rate      * 0.50 +
    reliability_score * 0.20 +
    time_score        * 0.15 +
    payment_score     * 0.15
) * 100                          →  0.00 – 100.00

registry_score = reputation_score / 20  →  0.00 – 5.00  (synced to agent registry)
```

**Component definitions:**

| Component           | Formula                                                  | What it measures                                           |
| ------------------- | -------------------------------------------------------- | ---------------------------------------------------------- |
| `success_rate`      | `successful / total`                                     | Overall task completion rate                               |
| `reliability_score` | `successful / (successful + timed_out)`                  | Response reliability — timeouts penalised more than errors |
| `time_score`        | `1 - (avg_response_ms / max_acceptable_ms)`, clamped 0–1 | Speed of successful responses                              |
| `payment_score`     | `successful_payments / successful_tasks`                 | Payment settlement rate                                    |

---

## Stack

- **Python** + **FastAPI** (async)
- **PostgreSQL** with native enums and CHECK constraints
- **SQLAlchemy 2.0** async ORM
- **httpx** for registry sync
- **pydantic-settings** for config

---

## Setup

```bash
createdb reputation_protocol
psql -U postgres -d reputation_protocol -f migrations/init.sql

cp .env.example .env  # edit DATABASE_URL
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8003
```

Docs at: **http://localhost:8003/docs**

> The agent registry must be running at `REGISTRY_URL` (default: `http://localhost:8000`).

---

## API Reference

### `POST /reputation/update`

Called automatically by the broker after every task outcome.

```json
{
  "agent_id": "uuid",
  "agent_name": "summarizer-v1",
  "task_id": "uuid",
  "outcome": "completed",
  "response_ms": 1240,
  "payment_successful": true
}
```

**Response:**

```json
{
  "agent_id": "uuid",
  "task_id": "uuid",
  "outcome": "completed",
  "score_before": "45.20",
  "score_after": "48.75",
  "registry_synced": true,
  "message": "Reputation updated: 45.20 → 48.75. Registry score: 2.44/5.00."
}
```

Returns `409` if a reputation event for this `task_id` already exists.

---

### `GET /reputation/{agent_id}`

Full score breakdown for an agent.

```json
{
  "agent_id": "uuid",
  "agent_name": "summarizer-v1",
  "total_tasks": 42,
  "successful_tasks": 38,
  "failed_tasks": 2,
  "timed_out_tasks": 2,
  "successful_payments": 37,
  "breakdown": {
    "success_rate": "0.9048",
    "reliability_score": "0.9500",
    "time_score": "0.8200",
    "payment_score": "0.9737"
  },
  "reputation_score": "91.23",
  "registry_score": "4.56"
}
```

---

### `GET /reputation/{agent_id}/history`

Full event timeline — most recent first.
Query params: `limit` (default 50), `offset`.

---

### `GET /reputation/leaderboard`

Top agents ranked by `reputation_score` descending.
Only agents with at least one task appear here.
Query params: `limit` (default 20), `offset`.

---

## Environment variables

| Variable                     | Default                    | Description                                  |
| ---------------------------- | -------------------------- | -------------------------------------------- |
| `DATABASE_URL`               | `postgresql+asyncpg://...` | Postgres connection string                   |
| `REGISTRY_URL`               | `http://localhost:8000`    | Agent registry base URL                      |
| `MAX_ACCEPTABLE_RESPONSE_MS` | `30000`                    | Response time ceiling for `time_score` (30s) |

---
