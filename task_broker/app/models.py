import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime, Enum, ForeignKey,
    SmallInteger, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    capability_required: Mapped[str] = mapped_column(String(100), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("pending", "assigned", "completed", "failed", "cancelled",
             name="task_status", create_type=False),
        nullable=False, default="pending",
    )
    result_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    submitted_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    timeout_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=30)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    attempts: Mapped[list["TaskAttempt"]] = relationship(
        "TaskAttempt",
        back_populates="task",
        order_by="TaskAttempt.attempt_number",
        cascade="all, delete-orphan",
    )


class TaskAttempt(Base):
    __tablename__ = "task_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Agent fields are snapshotted at dispatch time — the registry record
    # may change or the agent may deregister; the attempt record must be stable.
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("dispatched", "succeeded", "failed", "timed_out",
             name="attempt_status", create_type=False),
        nullable=False, default="dispatched",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    task: Mapped["Task"] = relationship("Task", back_populates="attempts")