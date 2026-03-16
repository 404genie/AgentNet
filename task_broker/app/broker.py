"""
broker.py — The core dispatch engine.

Responsibilities:
  1. Query the agent registry for agents that support the required capability,
     sorted by reputation (highest first).
  2. Try each agent in order, up to max_attempts.
  3. For each attempt: create an attempt record, call the agent's endpoint,
     enforce the timeout, validate the response, and record the outcome.
  4. On first success: mark the task completed, store the result.
  5. If all attempts fail: mark the task failed with a consolidated error message.

The broker never assumes any agent is reliable:
  - Every HTTP error, timeout, and invalid response is caught and recorded.
  - The fallback chain continues as long as capable agents remain.
  - Agent fields are snapshotted at dispatch time — not re-fetched mid-chain.
"""

import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import settings
from app.models import Task
from app.schemas import RegistryAgent

logger = logging.getLogger(__name__)


# ── Registry lookup ────────────────────────────────────────────────────────────

async def fetch_capable_agents(capability: str) -> list[RegistryAgent]:
    """
    Query the agent registry for agents supporting `capability`,
    sorted by reputation descending. Returns an empty list on any error
    rather than raising — a registry failure is treated as 'no agents found'.
    """
    params = {
        "capability": capability,
        "sort_by": "reputation",
        "order": "desc",
        "limit": 10,  # Fetch top 10; we'll cap attempts at task.max_attempts
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.registry_url}/search_agents",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            return [RegistryAgent(**a) for a in data.get("agents", [])]
    except Exception as exc:
        logger.error("Registry lookup failed for capability '%s': %s", capability, exc)
        return []


# ── Response validation ────────────────────────────────────────────────────────

def _validate_agent_response(response_data: dict, agent: RegistryAgent, capability: str) -> str | None:
    """
    Validate the agent's response against its declared capability_schema.
    Returns an error string if invalid, None if valid.

    The agent's response must be a JSON object containing at minimum an
    'output' key. If the agent declared an output_type in its capability_schema,
    we do a lightweight type check on that value.
    """
    if "output" not in response_data:
        return "Agent response missing required 'output' key."

    schema = agent.capability_schema.get(capability)
    if schema is None:
        # Agent didn't declare a schema for this capability — accept any output
        return None

    output_type = schema.get("output_type")
    output = response_data["output"]

    type_checks = {
        "text":    lambda v: isinstance(v, str),
        "json":    lambda v: isinstance(v, (dict, list)),
        "number":  lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "image":   lambda v: isinstance(v, str),   # base64 string
        "audio":   lambda v: isinstance(v, str),   # base64 string
        "binary":  lambda v: isinstance(v, str),   # base64 string
    }

    check = type_checks.get(output_type)
    if check and not check(output):
        return (
            f"Agent output type mismatch: expected '{output_type}' "
            f"but got '{type(output).__name__}'."
        )
    return None


# ── Single-agent dispatch ──────────────────────────────────────────────────────

async def _dispatch_to_agent(
    db: AsyncSession,
    task: Task,
    agent: RegistryAgent,
    attempt_number: int,
) -> dict | None:
    """
    Dispatch the task to a single agent and record the attempt.

    Returns the result payload dict on success, None on any failure.
    All outcomes (success, failure, timeout) are persisted to task_attempts.
    """
    attempt = await crud.create_attempt(
        db=db,
        task_id=task.id,
        agent_id=agent.id,
        agent_name=agent.name,
        agent_endpoint=agent.endpoint,
        attempt_number=attempt_number,
    )

    payload = {
        "task_id": str(task.id),
        "capability": task.capability_required,
        "input": task.input_payload,
    }

    try:
        async with httpx.AsyncClient(timeout=task.timeout_seconds) as client:
            resp = await client.post(agent.endpoint, json=payload)
            resp.raise_for_status()
            response_data = resp.json()

    except httpx.TimeoutException:
        logger.warning(
            "Task %s: agent '%s' timed out after %ds (attempt %d)",
            task.id, agent.name, task.timeout_seconds, attempt_number,
        )
        await crud.resolve_attempt(db, attempt, "timed_out", "Agent timed out.")
        return None

    except httpx.HTTPStatusError as exc:
        error = f"Agent returned HTTP {exc.response.status_code}."
        logger.warning(
            "Task %s: agent '%s' returned error %s (attempt %d)",
            task.id, agent.name, exc.response.status_code, attempt_number,
        )
        await crud.resolve_attempt(db, attempt, "failed", error)
        return None

    except Exception as exc:
        error = f"Unexpected error calling agent: {exc}"
        logger.error("Task %s: agent '%s' error: %s", task.id, agent.name, exc)
        await crud.resolve_attempt(db, attempt, "failed", error)
        return None

    # Validate the response structure and output type
    validation_error = _validate_agent_response(
        response_data, agent, task.capability_required
    )
    if validation_error:
        logger.warning(
            "Task %s: agent '%s' returned invalid response: %s (attempt %d)",
            task.id, agent.name, validation_error, attempt_number,
        )
        await crud.resolve_attempt(db, attempt, "failed", validation_error)
        return None

    # Success
    await crud.resolve_attempt(db, attempt, "succeeded")
    logger.info(
        "Task %s: agent '%s' succeeded (attempt %d)",
        task.id, agent.name, attempt_number,
    )
    return response_data


# ── Main dispatch entry point ──────────────────────────────────────────────────

async def dispatch_task(db: AsyncSession, task: Task) -> None:
    """
    Full dispatch lifecycle for a task:
      1. Fetch capable agents from registry (sorted by reputation).
      2. Try each agent in order up to task.max_attempts.
      3. On first success: mark task completed.
      4. If all fail: mark task failed with aggregated error context.

    This function is called after the task row is committed, so the task
    already exists in the DB before any attempt is recorded.
    """
    # Mark task as assigned before first dispatch
    await crud.update_task_status(db, task, "assigned")

    agents = await fetch_capable_agents(task.capability_required)

    if not agents:
        await crud.update_task_status(
            db, task, "failed",
            error_message=(
                f"No agents found in registry supporting "
                f"capability '{task.capability_required}'."
            ),
        )
        logger.warning("Task %s: no capable agents found.", task.id)
        return

    # Cap the number of agents to try at task.max_attempts
    candidates = agents[: task.max_attempts]
    errors: list[str] = []

    for attempt_number, agent in enumerate(candidates, start=1):
        result = await _dispatch_to_agent(db, task, agent, attempt_number)

        if result is not None:
            await crud.update_task_status(
                db, task, "completed", result_payload=result
            )
            return

        # Record which agent failed for the consolidated error message
        errors.append(f"Attempt {attempt_number} ({agent.name}): failed or timed out.")

    # All candidates exhausted
    consolidated = " | ".join(errors)
    await crud.update_task_status(
        db, task, "failed",
        error_message=f"All {len(candidates)} attempt(s) failed. {consolidated}",
    )
    logger.error("Task %s: all %d attempt(s) failed.", task.id, len(candidates))