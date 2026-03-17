import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from app.database import engine, Base, get_db
from app.schemas import (
    RegisterAgentRequest,
    AgentResponse,
    AgentListResponse,
    SearchAgentsParams,
    UpdateReputationRequest,
)
from app import crud



# ── Lifespan: create tables on startup ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates tables from ORM metadata — for dev convenience only.
    # NOTE: This does NOT apply the reputation CHECK constraint, pgcrypto
    # extension, GIN indexes, or the updated_at trigger from init.sql.
    # For production or a fresh environment, always run migrations/init.sql first.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Agent Registry",
    description=(
        "Discovery protocol for the AI Agent Economy. "
        "Agents register their capabilities here, and orchestrators search "
        "for agents to hire."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post(
    "/register_agent",
    response_model=AgentResponse,
    status_code=201,
    summary="Register a new agent",
    tags=["Registry"],
)
async def register_agent(
    body: RegisterAgentRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Register an AI agent in the discovery registry.

    - **name**: Unique identifier for this agent (e.g. `summarizer-v1`)
    - **endpoint**: URL where this agent accepts task requests
    - **capabilities**: List of capability tags (e.g. `["summarization", "translation"]`)
    - **price**: Cost per task in credits

    Reputation starts at `0.00` and is updated by the reputation protocol.
    """
    try:
        agent = await crud.create_agent(db, body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return agent


@app.get(
    "/search_agents",
    response_model=AgentListResponse,
    summary="Search agents by capability",
    tags=["Discovery"],
)
async def search_agents(
    capability: str | None = Query(
        default=None,
        description="Filter by capability tag (e.g. 'summarization')",
    ),
    sort_by: Literal["reputation", "price"] = Query(
        default="reputation",
        description="Sort results by reputation or price",
    ),
    order: Literal["asc", "desc"] = Query(
        default="desc",
        description="asc or desc",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Discover agents in the registry.

    Filter by **capability**, sort by **reputation** (default) or **price**,
    and paginate with **limit** / **offset**.
    """
    params = SearchAgentsParams(
        capability=capability,
        sort_by=sort_by,
        order=order,
        limit=limit,
        offset=offset,
    )
    agents, total = await crud.search_agents(db, params)
    return AgentListResponse(agents=agents, total=total)


@app.get(
    "/agent/{agent_id}",
    response_model=AgentResponse,
    summary="Get agent by ID",
    tags=["Registry"],
)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve full details for a specific agent by its UUID.
    Used by orchestrators after discovery to confirm terms before contracting.
    """
    agent = await crud.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
    return agent


# ── PATCH /agents/{agent_id}/reputation ───────────────────────────────────────

@app.patch(
    "/agents/{agent_id}/reputation",
    response_model=AgentResponse,
    summary="Update agent reputation score",
    tags=["Registry"],
)
async def update_reputation(
    agent_id: uuid.UUID,
    body: UpdateReputationRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the reputation protocol after every task outcome.
    Updates the agent's reputation score (0.00–5.00) in the registry
    so that search results always reflect current reputation.
    """
    agent = await crud.update_agent_reputation(db, agent_id, body.reputation)
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found."
        )
    return agent


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return JSONResponse({"status": "ok"})