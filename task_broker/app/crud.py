import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Task, TaskAttempt
from app.schemas import SubmitTaskRequest, ListTasksParams


# ── Create task ────────────────────────────────────────────────────────────────

async def create_task(db: AsyncSession, data: SubmitTaskRequest) -> Task:
    task = Task(
        capability_required=data.capability_required,
        input_payload=data.input_payload,
        submitted_by=data.submitted_by,
        max_attempts=data.max_attempts,
        timeout_seconds=data.timeout_seconds,
        status="pending",
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


# ── Get task by ID (with attempts eagerly loaded) ─────────────────────────────

async def get_task(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    result = await db.execute(
        select(Task)
        .options(selectinload(Task.attempts))
        .where(Task.id == task_id)
    )
    return result.scalar_one_or_none()


# ── List tasks ────────────────────────────────────────────────────────────────

async def list_tasks(
    db: AsyncSession, params: ListTasksParams
) -> tuple[list[Task], int]:
    query = select(Task).options(selectinload(Task.attempts))

    if params.status:
        query = query.where(Task.status == params.status)
    if params.submitted_by:
        query = query.where(Task.submitted_by == params.submitted_by)
    if params.capability:
        query = query.where(
            Task.capability_required == params.capability.strip().lower()
        )

    count_query = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_query)).scalar_one()

    query = query.order_by(Task.created_at.desc()).offset(params.offset).limit(params.limit)
    result = await db.execute(query)
    return list(result.scalars().all()), total


# ── Update task status ────────────────────────────────────────────────────────

async def update_task_status(
    db: AsyncSession,
    task: Task,
    status: str,
    result_payload: dict | None = None,
    error_message: str | None = None,
) -> Task:
    task.status = status
    if result_payload is not None:
        task.result_payload = result_payload
    if error_message is not None:
        task.error_message = error_message
    await db.flush()
    return task


# ── Cancel task ───────────────────────────────────────────────────────────────

async def cancel_task(db: AsyncSession, task: Task) -> Task:
    """
    Cancel a task. Only valid from 'pending' status.
    Returns the updated task, or raises ValueError if not cancellable.
    """
    if task.status != "pending":
        raise ValueError(
            f"Task cannot be cancelled — current status is '{task.status}'. "
            "Only pending tasks can be cancelled."
        )
    task.status = "cancelled"
    await db.flush()
    return task


# ── Create attempt ─────────────────────────────────────────────────────────────

async def create_attempt(
    db: AsyncSession,
    task_id: uuid.UUID,
    agent_id: uuid.UUID,
    agent_name: str,
    agent_endpoint: str,
    attempt_number: int,
) -> TaskAttempt:
    attempt = TaskAttempt(
        task_id=task_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_endpoint=agent_endpoint,
        attempt_number=attempt_number,
        status="dispatched",
    )
    db.add(attempt)
    await db.flush()
    return attempt


# ── Resolve attempt ────────────────────────────────────────────────────────────

async def resolve_attempt(
    db: AsyncSession,
    attempt: TaskAttempt,
    status: str,
    error_message: str | None = None,
) -> TaskAttempt:
    attempt.status = status
    attempt.completed_at = datetime.now(timezone.utc)
    if error_message is not None:
        attempt.error_message = error_message
    await db.flush()
    return attempt