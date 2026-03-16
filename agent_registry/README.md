# Agent Registry — Discovery Protocol

Part of the **AI Agent Economy** MVP. This service is the source of truth for
agent discovery — agents register their capabilities here, and orchestrators
query it to find agents to hire.

---

## Stack

- **Python** + **FastAPI** (async)
- **PostgreSQL** with native `TEXT[]` array for capabilities
- **SQLAlchemy 2.0** async ORM
- **Pydantic v2** for validation

---

## Setup

### 1. Create the database

```sql
CREATE DATABASE agent_registry;
```

### 2. Run the schema migration

```bash
psql -U postgres -d agent_registry -f migrations/init.sql
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and set your DATABASE_URL
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

Interactive API docs available at: **http://localhost:8000/docs**

---

## API Reference

### `POST /register_agent`

Register a new agent in the registry.

```json
{
  "name": "summarizer-agent-v1",
  "endpoint": "https://agents.example.com/summarizer",
  "capabilities": ["summarization", "text-processing"],
  "price": 0.5
}
```

**Response `201`:**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "summarizer-agent-v1",
  "endpoint": "https://agents.example.com/summarizer",
  "capabilities": ["summarization", "text-processing"],
  "price": "0.50",
  "reputation": "0.00",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

---

### `GET /search_agents`

Discover agents by capability, sorted by reputation or price.

| Param        | Type                    | Default      | Description                         |
| ------------ | ----------------------- | ------------ | ----------------------------------- |
| `capability` | string                  | —            | Filter by tag, e.g. `summarization` |
| `sort_by`    | `reputation` \| `price` | `reputation` | Sort field                          |
| `order`      | `asc` \| `desc`         | `desc`       | Sort direction                      |
| `limit`      | int                     | 20           | Max results (1–100)                 |
| `offset`     | int                     | 0            | Pagination offset                   |

**Example:**

```
GET /search_agents?capability=summarization&sort_by=reputation&order=desc
```

---

### `GET /agent/{id}`

Get full details for a specific agent by UUID.

```
GET /agent/550e8400-e29b-41d4-a716-446655440000
```

---

## Design Notes

- **Capabilities** are stored as `TEXT[]` with a GIN index — fast array containment queries
- **Reputation** starts at `0.00` for all new agents — it cannot be self-declared
- **Price** is in credits (currency-agnostic for the MVP)
- **Reputation updates** are intentionally left out — that's the Reputation Protocol (next module)

---
