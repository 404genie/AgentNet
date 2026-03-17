import uuid
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReputationScore, ReputationEvent
from app.schemas import ReputationUpdateRequest
from app.scoring import compute_scores
from app.database import settings


# ── Get or create reputation record ───────────────────────────────────────────

async def get_score_by_agent_id(
    db: AsyncSession, agent_id: uuid.UUID
) -> ReputationScore | None:
    result = await db.execute(
        select(ReputationScore).where(ReputationScore.agent_id == agent_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_score(
    db: AsyncSession, agent_id: uuid.UUID, agent_name: str
) -> ReputationScore:
    """Fetch existing record or create a zeroed-out one for a new agent."""
    record = await get_score_by_agent_id(db, agent_id)
    if record is None:
        record = ReputationScore(
            agent_id=agent_id,
            agent_name=agent_name,
        )
        db.add(record)
        await db.flush()
        await db.refresh(record)
    return record


# ── Apply a reputation event ───────────────────────────────────────────────────

async def apply_event(
    db: AsyncSession,
    data: ReputationUpdateRequest,
) -> tuple[ReputationScore, ReputationEvent]:
    """
    Apply one task outcome to the agent's reputation:
      1. Get or create the reputation record.
      2. Increment the relevant counters.
      3. Recompute all score components.
      4. Persist the event with before/after scores.
      5. Return updated record and event.
    """
    record = await get_or_create_score(db, data.agent_id, data.agent_name)
    score_before = record.reputation_score

    # ── Increment counters ────────────────────────────────────────────────────
    record.total_tasks += 1

    if data.outcome == "completed":
        record.successful_tasks += 1
        if data.response_ms is not None:
            record.total_response_ms += data.response_ms
        if data.payment_successful:
            record.successful_payments += 1
    elif data.outcome == "timed_out":
        record.timed_out_tasks += 1
    else:  # failed
        record.failed_tasks += 1

    # ── Recompute scores ──────────────────────────────────────────────────────
    scores = compute_scores(
        total_tasks=record.total_tasks,
        successful_tasks=record.successful_tasks,
        timed_out_tasks=record.timed_out_tasks,
        total_response_ms=record.total_response_ms,
        successful_payments=record.successful_payments,
        max_acceptable_ms=settings.max_acceptable_response_ms,
    )
    record.success_rate       = scores["success_rate"]
    record.reliability_score  = scores["reliability_score"]
    record.time_score         = scores["time_score"]
    record.payment_score      = scores["payment_score"]
    record.reputation_score   = scores["reputation_score"]
    record.registry_score     = scores["registry_score"]

    # ── Record the event ──────────────────────────────────────────────────────
    event = ReputationEvent(
        agent_id=data.agent_id,
        task_id=data.task_id,
        outcome=data.outcome,
        response_ms=data.response_ms,
        payment_successful=data.payment_successful,
        score_before=score_before,
        score_after=scores["reputation_score"],
    )
    db.add(event)
    await db.flush()
    await db.refresh(record)
    return record, event


# ── History ────────────────────────────────────────────────────────────────────

async def get_event_history(
    db: AsyncSession,
    agent_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ReputationEvent], int]:
    query = (
        select(ReputationEvent)
        .where(ReputationEvent.agent_id == agent_id)
        .order_by(ReputationEvent.created_at.desc())
    )

    count_query = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_query)).scalar_one()

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all()), total


# ── Leaderboard ────────────────────────────────────────────────────────────────

async def get_leaderboard(
    db: AsyncSession,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ReputationScore], int]:
    query = (
        select(ReputationScore)
        .where(ReputationScore.total_tasks > 0)
        .order_by(ReputationScore.reputation_score.desc())
    )

    count_query = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_query)).scalar_one()

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all()), total