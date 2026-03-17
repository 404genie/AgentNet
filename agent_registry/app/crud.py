import uuid
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import Agent
from app.schemas import RegisterAgentRequest, SearchAgentsParams


# ── Register ───────────────────────────────────────────────────────────────────

async def create_agent(db: AsyncSession, data: RegisterAgentRequest) -> Agent:
    """
    Insert a new agent into the registry.
    Raises ValueError on duplicate name.
    """
    # Serialize capability_schema: Pydantic models -> plain dicts for JSONB
    serialized_schema = {
        tag: entry.model_dump(mode="json")
        for tag, entry in data.capability_schema.items()
    }

    agent = Agent(
        name=data.name,
        endpoint=str(data.endpoint),
        capabilities=data.capabilities,
        price=data.price,
        reputation=Decimal("0.00"),  # Always starts at 0 — earned, not declared
        agent_version=data.agent_version,
        capability_schema=serialized_schema,
    )
    db.add(agent)
    try:
        await db.flush()        # Catch constraint errors before commit
    except IntegrityError:
        # Let get_db's context manager handle the rollback — don't call it twice
        raise ValueError(f"Agent name '{data.name}' is already registered.")
    await db.refresh(agent)
    return agent


# ── Search ─────────────────────────────────────────────────────────────────────

async def search_agents(
    db: AsyncSession, params: SearchAgentsParams
) -> tuple[list[Agent], int]:
    """
    Search agents with optional capability filter, sorting, and pagination.
    Returns (agents, total_count).
    """
    base_query = select(Agent)

    # Filter by capability tag if provided
    if params.capability:
        tag = params.capability.strip().lower()
        base_query = base_query.where(Agent.capabilities.any(tag))

    # Count total matching rows (before pagination)
    count_query = select(func.count()).select_from(base_query.subquery())
    total: int = (await db.execute(count_query)).scalar_one()

    # Apply sort
    sort_col = Agent.reputation if params.sort_by == "reputation" else Agent.price
    if params.order == "desc":
        base_query = base_query.order_by(sort_col.desc())
    else:
        base_query = base_query.order_by(sort_col.asc())

    # Apply pagination
    base_query = base_query.offset(params.offset).limit(params.limit)

    result = await db.execute(base_query)
    agents = list(result.scalars().all())

    return agents, total


# ── Get by ID ──────────────────────────────────────────────────────────────────

async def get_agent_by_id(db: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
    """Fetch a single agent by its UUID. Returns None if not found."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    return result.scalar_one_or_none()


# ── Update reputation ──────────────────────────────────────────────────────────

async def update_agent_reputation(
    db: AsyncSession,
    agent_id: uuid.UUID,
    reputation: Decimal,
) -> Agent | None:
    """
    Update the reputation score for an agent.
    Called by the reputation protocol after each task outcome.
    Returns the updated agent, or None if not found.
    """
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        return None
    agent.reputation = reputation
    await db.flush()
    await db.refresh(agent)
    return agent