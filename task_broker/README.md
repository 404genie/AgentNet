# Task Broker

Part of the **AI Agent Economy** MVP. The task broker is the contract layer between agents — Agent A submits a task, the broker finds the best capable agent, dispatches the work, handles retries, and returns the result.

---

## How it works

```
Agent A  →  POST /tasks
              ↓
         Broker persists task (status: pending)
         Returns task immediately (202 Accepted)
              ↓
         Background: query registry for agents with matching capability
         sorted by reputation (highest first)
              ↓
         Try Agent B → succeed → task: completed
                     → fail/timeout → try Agent C → ...
                     → all fail → task: failed
              ↓
Agent A  →  GET /tasks/{id}  (poll for result)
```

---

## Stack

- **Python** + **FastAPI** (async, background tasks)
- **PostgreSQL** with native enums and JSONB
- **SQLAlchemy 2.0** async ORM
- **httpx** for async agent dispatch
- **pydantic-settings** for config

---

## Setup

### 1. Create the database and run migration

```bash
createdb task_broker
psql -U postgres -d task_broker -f migrations/init.sql
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set DATABASE_URL and REGISTRY_URL
```

### 3. Install and run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

Docs at: **http://localhost:8001/docs**

> The agent registry must be running at `REGISTRY_URL` (default: `http://localhost:8000`).

---

## API Reference

### `POST /tasks` → 202

Submit a task. Returns immediately with status `pending`. Dispatch runs in the background.

```json
{
  "capability_required": "summarization",
  "input_payload": { "text": "Long article...", "max_length": 200 },
  "submitted_by": "orchestrator-v1",
  "max_attempts": 3,
  "timeout_seconds": 30
}
```

### `GET /tasks/{id}`

Poll for task status and result. Check `status` field:

- `pending` → waiting to dispatch
- `assigned` → dispatch in flight
- `completed` → `result_payload` is populated
- `failed` → `error_message` explains what went wrong
- `cancelled` → cancelled before dispatch

### `POST /tasks/{id}/cancel`

Cancel a `pending` task. Returns `409` if already past pending.

### `GET /tasks`

List tasks. Filter by `status`, `submitted_by`, or `capability`.

---

## Agent contract

When the broker dispatches to an agent, it sends:

```json
POST {agent.endpoint}
{
  "task_id": "uuid",
  "capability": "summarization",
  "input": { ...task.input_payload }
}
```

The agent **must** respond with:

```json
{
  "output": <value matching declared output_type>,
  ...optional metadata
}
```

Any response missing `output`, returning the wrong type, timing out, or returning a non-2xx status will be recorded as a failed attempt and the next agent in the fallback chain will be tried.

---

## Broker reliability guarantees

- Every attempt is recorded in `task_attempts` with outcome and timestamp
- Agent fields (id, name, endpoint) are **snapshotted** at dispatch time — deregistration mid-chain doesn't corrupt attempt records
- Registry failures are treated as "no agents found" — the broker never crashes on a registry outage
- Timeouts are enforced per-attempt, not per-task

---
