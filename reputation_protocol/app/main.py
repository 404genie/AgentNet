import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app import crud
from app.models import ReputationEvent
from app.database import Base, engine, get_db, settings
logger = logging.getLogger(__name__)

from app.schemas import (
    LeaderboardEntry,
    LeaderboardResponse,
    ReputationEventListResponse,
    ReputationScoreResponse,
    ReputationUpdateRequest,
    ReputationUpdateResponse,
)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates tables from ORM metadata — for dev convenience only.
    # For production, run migrations/init.sql.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Reputation Protocol",
    description=(
        "Reputation scoring service for the AI Agent Economy. "
        "Tracks task outcomes per agent, computes weighted reputation scores, "
        "and syncs scores back to the agent registry."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ── Registry sync helper ───────────────────────────────────────────────────────

async def _sync_to_registry(agent_id: uuid.UUID, registry_score: float) -> bool:
    """
    PATCH /agents/{agent_id}/reputation on the agent registry to keep
    reputation scores in sync so search results reflect current scores.
    Returns True on success, False on failure (logged, not fatal).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{settings.registry_url}/agents/{agent_id}/reputation",
                json={"reputation": registry_score},
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error(
            "Failed to sync reputation for agent '%s' to registry: %s",
            agent_id, exc,
        )
        return False


# ── POST /reputation/update ────────────────────────────────────────────────────

@app.post(
    "/reputation/update",
    response_model=ReputationUpdateResponse,
    status_code=200,
    summary="Record a task outcome and update reputation",
    tags=["Reputation"],
)
async def update_reputation(
    body: ReputationUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Called automatically by the broker after a task completes or fails.

    - Increments outcome counters for the agent
    - Recomputes all score components
    - Records the event with score before/after
    - Syncs the new registry_score (0–5) back to the agent registry
    """
    # Guard: duplicate task_id — each task can only generate one event
    existing = await db.execute(
        select(ReputationEvent).where(ReputationEvent.task_id == body.task_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Reputation event for task '{body.task_id}' already recorded.",
        )

    record, event = await crud.apply_event(db, body)

    # Sync to registry — failure is logged but doesn't fail the request
    synced = await _sync_to_registry(
        body.agent_id, float(record.registry_score)
    )

    return ReputationUpdateResponse(
        agent_id=body.agent_id,
        task_id=body.task_id,
        outcome=body.outcome,
        score_before=event.score_before,
        score_after=event.score_after,
        registry_synced=synced,
        message=(
            f"Reputation updated: {event.score_before} → {event.score_after}. "
            f"Registry score: {record.registry_score}/5.00."
        ),
    )


# ── GET /reputation/{agent_id} ─────────────────────────────────────────────────

@app.get(
    "/reputation/{agent_id}",
    response_model=ReputationScoreResponse,
    summary="Get reputation score and breakdown for an agent",
    tags=["Reputation"],
)
async def get_reputation(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the full reputation breakdown:
    total tasks, per-outcome counts, each score component, and final scores.
    """
    record = await crud.get_score_by_agent_id(db, agent_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No reputation record found for agent '{agent_id}'.",
        )
    return ReputationScoreResponse.from_orm_with_breakdown(record)


# ── GET /reputation/{agent_id}/history ────────────────────────────────────────

@app.get(
    "/reputation/{agent_id}/history",
    response_model=ReputationEventListResponse,
    summary="Get reputation event history for an agent",
    tags=["Reputation"],
)
async def get_reputation_history(
    agent_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the full timeline of task outcomes that shaped this agent's score,
    most recent first.
    """
    record = await crud.get_score_by_agent_id(db, agent_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No reputation record found for agent '{agent_id}'.",
        )
    events, total = await crud.get_event_history(db, agent_id, limit, offset)
    return ReputationEventListResponse(events=events, total=total)


# ── GET /reputation/leaderboard ────────────────────────────────────────────────

@app.get(
    "/reputation/leaderboard",
    response_model=LeaderboardResponse,
    summary="Top agents by reputation score",
    tags=["Reputation"],
)
async def get_leaderboard(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns agents ranked by reputation_score descending.
    Only agents with at least one completed task appear here.
    """
    records, total = await crud.get_leaderboard(db, limit, offset)
    entries = [
        LeaderboardEntry(
            rank=offset + i + 1,
            agent_id=r.agent_id,
            agent_name=r.agent_name,
            reputation_score=r.reputation_score,
            registry_score=r.registry_score,
            total_tasks=r.total_tasks,
            successful_tasks=r.successful_tasks,
        )
        for i, r in enumerate(records)
    ]
    return LeaderboardResponse(entries=entries, total=total)


# ── GET /health ────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return JSONResponse({"status": "ok"})