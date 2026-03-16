"""
broker.py — The core dispatch engine.

Responsibilities:
  1. Pre-flight: verify Agent A has sufficient balance before dispatch begins.
  2. Hold funds from Agent A for the selected Agent B's price.
  3. Query the agent registry for capable agents sorted by reputation.
  4. Try each agent in order up to max_attempts.
  5. For each attempt: create attempt record, call agent endpoint,
     enforce timeout, validate response, record outcome.
  6. On first success: call payment railway to settle — then mark task completed.
  7. On all failures: release the hold — then mark task failed.

The broker never assumes any agent is reliable:
  - Every HTTP error, timeout, and invalid response is caught and recorded.
  - The fallback chain continues as long as capable agents remain.
  - Agent fields are snapshotted at dispatch time — not re-fetched mid-chain.
"""

import logging
import uuid
from decimal import Decimal

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
    sorted by reputation descending. Returns empty list on any error —
    a registry failure is treated as 'no agents found'.
    """
    params = {
        "capability": capability,
        "sort_by": "reputation",
        "order": "desc",
        "limit": 10,
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


# ── Payment helpers ────────────────────────────────────────────────────────────

async def _check_balance(
    payer_agent_id: uuid.UUID,
    required: Decimal,
) -> tuple[bool, str | None]:
    """
    GET /wallets/{agent_id} and verify available_balance >= required.
    Returns (sufficient, error_message).
    A payment service failure is treated as insufficient — tasks must not
    proceed if we cannot verify the payer can afford them.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.payment_url}/wallets/{payer_agent_id}",
            )
            if resp.status_code == 404:
                return False, (
                    f"No wallet found for agent '{payer_agent_id}'. "
                    "Agent must have a funded wallet before submitting tasks."
                )
            resp.raise_for_status()
            data = resp.json()
            available = Decimal(str(data["available_balance"]))
            if available < required:
                return False, (
                    f"Insufficient funds: {available} credits available, "
                    f"{required} required."
                )
            return True, None
    except Exception as exc:
        logger.error("Balance check failed for agent '%s': %s", payer_agent_id, exc)
        return False, "Payment service unavailable — cannot verify balance."


async def _hold_funds(
    task_id: uuid.UUID,
    payer_agent_id: uuid.UUID,
    payee_agent_id: uuid.UUID,
    amount: Decimal,
) -> bool:
    """
    POST /payments/hold — reserve funds before dispatch.
    Returns True on success, False on any failure.
    """
    payload = {
        "task_id": str(task_id),
        "payer_agent_id": str(payer_agent_id),
        "payee_agent_id": str(payee_agent_id),
        "amount": str(amount),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.payment_url}/payments/hold",
                json=payload,
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Hold failed for task '%s': %s", task_id, exc)
        return False


async def _settle_payment(task_id: uuid.UUID) -> bool:
    """
    POST /payments/settle — transfer held funds to payee after task completes.
    The settle endpoint only needs task_id — it resolves payer/payee
    from the existing hold record.
    Returns True on success, False on failure (logged, not fatal).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.payment_url}/payments/settle",
                json={"task_id": str(task_id)},
            )
            resp.raise_for_status()
            logger.info("Task %s: payment settled.", task_id)
            return True
    except Exception as exc:
        logger.error(
            "Task %s: payment settlement failed: %s. "
            "Task result is stored — payment requires manual reconciliation.",
            task_id, exc,
        )
        return False


async def _release_hold(task_id: uuid.UUID) -> None:
    """
    POST /payments/release — return held funds to payer on task failure.
    Failure is logged but does not change task status.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.payment_url}/payments/release",
                json={"task_id": str(task_id)},
            )
            resp.raise_for_status()
            logger.info("Task %s: hold released.", task_id)
    except Exception as exc:
        logger.error("Task %s: hold release failed: %s.", task_id, exc)


# ── Response validation ────────────────────────────────────────────────────────

def _validate_agent_response(
    response_data: dict, agent: RegistryAgent, capability: str
) -> str | None:
    """
    Validate the agent's response against its declared capability_schema.
    Returns an error string if invalid, None if valid.
    """
    if "output" not in response_data:
        return "Agent response missing required 'output' key."

    schema = agent.capability_schema.get(capability)
    if schema is None:
        return None

    output_type = schema.get("output_type")
    output = response_data["output"]

    type_checks = {
        "text":    lambda v: isinstance(v, str),
        "json":    lambda v: isinstance(v, (dict, list)),
        "number":  lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "image":   lambda v: isinstance(v, str),
        "audio":   lambda v: isinstance(v, str),
        "binary":  lambda v: isinstance(v, str),
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
      1. Fetch capable agents from registry.
      2. Pre-flight balance check against first agent's price.
      3. Hold funds for first agent.
      4. Try each agent in order up to max_attempts.
      5. On first success: settle payment, mark completed.
      6. On all failures: release hold, mark failed.
    """
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

    candidates = agents[: task.max_attempts]

    # Use the first (highest-reputation) agent's price for the hold.
    # All candidates are for the same capability — we hold against the
    # top candidate's price and keep it through the fallback chain.
    first_agent = candidates[0]
    amount = Decimal(str(first_agent.price))

    # Pre-flight: verify payer has enough balance
    sufficient, balance_error = await _check_balance(
        task.submitted_by_agent_id, amount
    )
    if not sufficient:
        await crud.update_task_status(
            db, task, "failed", error_message=balance_error,
        )
        logger.warning("Task %s: pre-flight failed — %s", task.id, balance_error)
        return

    # Hold funds before dispatch
    held = await _hold_funds(
        task_id=task.id,
        payer_agent_id=task.submitted_by_agent_id,
        payee_agent_id=first_agent.id,
        amount=amount,
    )
    if not held:
        await crud.update_task_status(
            db, task, "failed",
            error_message="Payment service failed to reserve funds — task aborted.",
        )
        return

    errors: list[str] = []

    for attempt_number, agent in enumerate(candidates, start=1):
        result = await _dispatch_to_agent(db, task, agent, attempt_number)

        if result is not None:
            # Settle first — result is real regardless of payment outcome
            await _settle_payment(task.id)
            await crud.update_task_status(
                db, task, "completed", result_payload=result
            )
            return

        errors.append(f"Attempt {attempt_number} ({agent.name}): failed or timed out.")

    # All candidates exhausted — release the hold
    await _release_hold(task.id)
    consolidated = " | ".join(errors)
    await crud.update_task_status(
        db, task, "failed",
        error_message=f"All {len(candidates)} attempt(s) failed. {consolidated}",
    )
    logger.error("Task %s: all %d attempt(s) failed.", task.id, len(candidates))