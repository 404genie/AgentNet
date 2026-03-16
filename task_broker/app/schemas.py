import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────────

TaskStatus = Literal["pending", "assigned", "completed", "failed", "cancelled"]
AttemptStatus = Literal["dispatched", "succeeded", "failed", "timed_out"]


# ── Request: submit a task ─────────────────────────────────────────────────────

class SubmitTaskRequest(BaseModel):
    capability_required: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="The capability tag the executing agent must support",
        examples=["summarization"],
    )
    input_payload: dict[str, Any] = Field(
        ...,
        description="The data to pass to the executing agent",
        examples=[{"text": "Summarize this article...", "max_length": 200}],
    )
    submitted_by: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable name of the agent submitting this task",
        examples=["orchestrator-agent-v1"],
    )
    submitted_by_agent_id: uuid.UUID = Field(
        ...,
        description="UUID of the submitting agent (must match their registry ID and have a wallet)",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max number of agents to try before marking the task failed",
    )
    timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Per-attempt timeout in seconds",
    )

    @field_validator("capability_required")
    @classmethod
    def normalize_capability(cls, v: str) -> str:
        normalized = v.strip().lower()
        if not normalized:
            raise ValueError("capability_required cannot be blank.")
        return normalized

    @field_validator("submitted_by")
    @classmethod
    def normalize_submitted_by(cls, v: str) -> str:
        return v.strip()


# ── Response: a single attempt ─────────────────────────────────────────────────

class TaskAttemptResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    attempt_number: int
    status: AttemptStatus
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


# ── Response: a task ──────────────────────────────────────────────────────────

class TaskResponse(BaseModel):
    id: uuid.UUID
    capability_required: str
    input_payload: dict[str, Any]
    status: TaskStatus
    result_payload: dict[str, Any] | None
    error_message: str | None
    submitted_by: str
    submitted_by_agent_id: uuid.UUID
    max_attempts: int
    timeout_seconds: int
    attempts: list[TaskAttemptResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Response: task list ────────────────────────────────────────────────────────

class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


# ── Query params: list tasks ───────────────────────────────────────────────────

class ListTasksParams(BaseModel):
    status: TaskStatus | None = Field(default=None)
    submitted_by: str | None = Field(default=None)
    capability: str | None = Field(default=None)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


# ── Internal: agent record from registry ──────────────────────────────────────

class RegistryAgent(BaseModel):
    id: uuid.UUID
    name: str
    endpoint: str
    capabilities: list[str]
    reputation: float
    price: float
    agent_version: str
    capability_schema: dict[str, Any]