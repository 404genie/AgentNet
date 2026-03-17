import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ReputationScore(Base):
    __tablename__ = "reputation_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    # agent_id mirrors the registry UUID — cross-service correlation
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True,
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── Raw counters ──────────────────────────────────────────────────────────
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timed_out_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Cumulative response time in ms — used to compute running average
    total_response_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Count of tasks where payment was successfully settled
    successful_payments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Computed score components (stored for transparency) ───────────────────
    success_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.0000"),
    )
    reliability_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.0000"),
    )
    time_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.0000"),
    )
    payment_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.0000"),
    )

    # ── Final scores ──────────────────────────────────────────────────────────
    # 0.00–100.00 — the weighted composite score
    reputation_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00"),
    )
    # 0.00–5.00 — mapped for the agent registry (reputation_score / 20)
    registry_score: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("0.00"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    events: Mapped[list["ReputationEvent"]] = relationship(
        "ReputationEvent",
        back_populates="score_record",
        order_by="ReputationEvent.created_at.desc()",
        cascade="all, delete-orphan",
    )


class ReputationEvent(Base):
    __tablename__ = "reputation_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True,
    )
    # completed | failed | timed_out
    outcome: Mapped[str] = mapped_column(
        Enum("completed", "failed", "timed_out",
             name="task_outcome", create_type=False),
        nullable=False,
    )
    # NULL for failed/timed_out — agent never responded
    response_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payment_successful: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    # Snapshot of scores before and after this event
    score_before: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00"),
    )
    score_after: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    score_record: Mapped["ReputationScore"] = relationship(
        "ReputationScore",
        back_populates="events",
        foreign_keys="[ReputationEvent.agent_id]",
        primaryjoin="ReputationEvent.agent_id == ReputationScore.agent_id",
    )