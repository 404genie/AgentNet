import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── Wallet ─────────────────────────────────────────────────────────────────────

class CreateWalletRequest(BaseModel):
    agent_id: uuid.UUID = Field(
        ..., description="UUID of the agent from the registry",
    )
    agent_name: str = Field(
        ..., min_length=1, max_length=100,
        description="Human-readable name for display purposes",
    )

    @field_validator("agent_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()


class TopUpRequest(BaseModel):
    amount: Decimal = Field(
        ..., gt=0, decimal_places=2,
        description="Credits to add to the wallet (must be positive)",
        examples=[50.00],
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Top-up amount must be greater than zero.")
        return v


class WalletResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    balance: Decimal
    held_balance: Decimal
    available_balance: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Payment operations ─────────────────────────────────────────────────────────

class HoldRequest(BaseModel):
    task_id: uuid.UUID = Field(..., description="Task ID from the task broker")
    payer_agent_id: uuid.UUID = Field(..., description="Agent A — the one paying")
    payee_agent_id: uuid.UUID = Field(..., description="Agent B — the one to be paid")
    amount: Decimal = Field(
        ..., gt=0, decimal_places=2,
        description="Amount to hold from payer's balance",
    )


class SettleRequest(BaseModel):
    task_id: uuid.UUID = Field(
        ..., description="Task ID — identifies the active hold to settle",
    )


class ReleaseRequest(BaseModel):
    task_id: uuid.UUID = Field(
        ..., description="Task ID — identifies the active hold to release",
    )


class PaymentResponse(BaseModel):
    task_id: uuid.UUID
    status: Literal["held", "settled", "released"]
    amount: Decimal
    payer_agent_id: uuid.UUID | None
    payee_agent_id: uuid.UUID
    message: str


# ── Transaction ────────────────────────────────────────────────────────────────

TxType = Literal["topup", "hold", "release", "settlement"]

class TransactionResponse(BaseModel):
    id: uuid.UUID
    from_agent_id: uuid.UUID | None
    to_agent_id: uuid.UUID
    task_id: uuid.UUID | None
    amount: Decimal
    tx_type: TxType
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    transactions: list[TransactionResponse]
    total: int