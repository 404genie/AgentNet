# Payment Railway

Part of the **AI Agent Economy** MVP. Manages agent wallets, reserves funds before task dispatch, settles payment on completion, and releases holds on failure.

---

## How it works

```
Admin          → POST /wallets                    create wallet, pre-funded
Broker         → GET  /wallets/{agent_id}         pre-flight balance check
Broker         → POST /payments/hold              reserve funds before dispatch
Task succeeds  → POST /payments/settle            transfer held funds to Agent B
Task fails     → POST /payments/release           return held funds to Agent A
Anyone         → GET  /payments/transactions      audit log
```

**Hold pattern:** funds are never deducted until a task succeeds. On failure the hold is released and the payer gets their full balance back.

---

## Stack

- **Python** + **FastAPI** (async)
- **PostgreSQL** with NUMERIC precision, CHECK constraints, and native enums
- **SQLAlchemy 2.0** async ORM
- **pydantic-settings** for config

---

## Setup

```bash
createdb payment_railway
psql -U postgres -d payment_railway -f migrations/init.sql

cp .env.example .env  # edit DATABASE_URL
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8002
```

Docs at: **http://localhost:8002/docs**

---

## API Reference

### Wallets

| Method | Path                        | Description                                                |
| ------ | --------------------------- | ---------------------------------------------------------- |
| `POST` | `/wallets`                  | Create wallet (admin) — pre-funded with `STARTING_BALANCE` |
| `GET`  | `/wallets/{agent_id}`       | Get balance, held amount, and available balance            |
| `POST` | `/wallets/{agent_id}/topup` | Add credits (admin)                                        |

### Payments

| Method | Path                     | Description                                     |
| ------ | ------------------------ | ----------------------------------------------- |
| `POST` | `/payments/hold`         | Reserve `amount` credits before task dispatch   |
| `POST` | `/payments/settle`       | Transfer held funds to payee on task completion |
| `POST` | `/payments/release`      | Return held funds to payer on task failure      |
| `GET`  | `/payments/transactions` | Audit log — filter by `agent_id` or `task_id`   |

---

## Wallet fields

```json
{
  "agent_id": "uuid",
  "balance": "100.00", // total credited funds (includes held)
  "held_balance": "20.00", // reserved for in-flight tasks
  "available_balance": "80.00" // spendable = balance - held_balance
}
```

---

## DB constraints

- `balance >= 0` — can never go negative
- `held_balance >= 0` — always non-negative
- `held_balance <= balance` — can never hold more than you have
- `amount > 0` — all holds and transactions must be positive

---
