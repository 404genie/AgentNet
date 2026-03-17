import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────────

Outcome = Literal["completed", "failed", "timed_out"]


# ── Request: reputation update from broker ────────────────────────────────────

class ReputationUpdateRequest(BaseModel):
    agent_id: uuid.UUID = Field(
        ..., description="Registry UUID of the agent being scored",
    )
    agent_name: str = Field(
        ..., min_length=1, max_length=100,
        description="Agent name — used to create the record if first event",
    )
    task_id: uuid.UUID = Field(
        ..., description="Task ID from the broker — must be unique per event",
    )
    outcome: Outcome = Field(
        ..., description="Task result: completed, failed, or timed_out",
    )
    response_ms: int | None = Field(
        default=None, ge=0,
        description="Time from dispatch to result in milliseconds. NULL for failed/timed_out.",
    )
    payment_successful: bool = Field(
        default=False,
        description="Whether payment was successfully settled for this task",
    )

    @field_validator("agent_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()



# ── Response: score breakdown ─────────────────────────────────────────────────

class ScoreBreakdown(BaseModel):
    success_rate: Decimal
    reliability_score: Decimal
    time_score: Decimal
    payment_score: Decimal


class ReputationScoreResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    timed_out_tasks: int
    successful_payments: int
    breakdown: ScoreBreakdown
    reputation_score: Decimal     # 0–100
    registry_score: Decimal       # 0–5
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_with_breakdown(cls, record: object) -> "ReputationScoreResponse":
        return cls(
            id=record.id,
            agent_id=record.agent_id,
            agent_name=record.agent_name,
            total_tasks=record.total_tasks,
            successful_tasks=record.successful_tasks,
            failed_tasks=record.failed_tasks,
            timed_out_tasks=record.timed_out_tasks,
            successful_payments=record.successful_payments,
            breakdown=ScoreBreakdown(
                success_rate=record.success_rate,
                reliability_score=record.reliability_score,
                time_score=record.time_score,
                payment_score=record.payment_score,
            ),
            reputation_score=record.reputation_score,
            registry_score=record.registry_score,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


# ── Response: a single reputation event ──────────────────────────────────────

class ReputationEventResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    task_id: uuid.UUID
    outcome: Outcome
    response_ms: int | None
    payment_successful: bool
    score_before: Decimal
    score_after: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


class ReputationEventListResponse(BaseModel):
    events: list[ReputationEventResponse]
    total: int


# ── Response: reputation update result ───────────────────────────────────────

class ReputationUpdateResponse(BaseModel):
    agent_id: uuid.UUID
    task_id: uuid.UUID
    outcome: Outcome
    score_before: Decimal
    score_after: Decimal
    registry_synced: bool
    message: str


# ── Response: leaderboard entry ───────────────────────────────────────────────

class LeaderboardEntry(BaseModel):
    rank: int
    agent_id: uuid.UUID
    agent_name: str
    reputation_score: Decimal
    registry_score: Decimal
    total_tasks: int
    successful_tasks: int

    model_config = {"from_attributes": True}


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    total: int