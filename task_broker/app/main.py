import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import broker, crud
from app.database import Base, engine, get_db
from app.schemas import (
    ListTasksParams,
    SubmitTaskRequest,
    TaskListResponse,
    TaskResponse,
)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates tables from ORM metadata — for dev convenience only.
    # For production, run migrations/init.sql which includes enums,
    # CHECK constraints, indexes, and the updated_at trigger.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Task Broker",
    description=(
        "Task contract layer for the AI Agent Economy. "
        "Agent A submits a task; the broker finds the best capable agent, "
        "dispatches the work, handles retries, and returns the result."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ── POST /tasks — submit a task ────────────────────────────────────────────────

@app.post(
    "/tasks",
    response_model=TaskResponse,
    status_code=202,
    summary="Submit a task",
    tags=["Tasks"],
)
async def submit_task(
    body: SubmitTaskRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a task to the broker.

    The broker immediately persists the task (status: `pending`), returns
    the task record, then dispatches to a capable agent in the background.

    Poll `GET /tasks/{id}` to check status and retrieve the result.
    """
    task = await crud.create_task(db, body)
    # get_db commits on teardown after the response is sent.
    # BackgroundTasks run after the response, so the task row is
    # already committed by the time _run_dispatch opens its own session.
    background_tasks.add_task(_run_dispatch, task.id)
    return task


async def _run_dispatch(task_id: uuid.UUID) -> None:
    """
    Background task wrapper — opens its own DB session so it runs
    independently from the request session that has already committed.
    """
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            task = await crud.get_task(db, task_id)
            if task is None:
                return
            await broker.dispatch_task(db, task)
            await db.commit()
        except Exception:
            await db.rollback()
            raise


# ── GET /tasks — list tasks ────────────────────────────────────────────────────

@app.get(
    "/tasks",
    response_model=TaskListResponse,
    summary="List tasks",
    tags=["Tasks"],
)
async def list_tasks(
    status: Literal["pending", "assigned", "completed", "failed", "cancelled"] | None = Query(default=None),
    submitted_by: str | None = Query(default=None),
    capability: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    List tasks with optional filtering by status, submitter, or capability.
    """
    params = ListTasksParams(
        status=status,
        submitted_by=submitted_by,
        capability=capability,
        limit=limit,
        offset=offset,
    )
    tasks, total = await crud.list_tasks(db, params)
    return TaskListResponse(tasks=tasks, total=total)


# ── GET /tasks/{task_id} — get task ───────────────────────────────────────────

@app.get(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    summary="Get task by ID",
    tags=["Tasks"],
)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve a task and its full attempt history by ID.

    Poll this endpoint to check whether the task has completed or failed.
    """
    task = await crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return task


# ── POST /tasks/{task_id}/cancel — cancel a task ──────────────────────────────

@app.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskResponse,
    summary="Cancel a task",
    tags=["Tasks"],
)
async def cancel_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel a pending task. Only tasks in `pending` status can be cancelled —
    once a task is `assigned` the dispatch is already in flight.
    """
    task = await crud.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    try:
        task = await crud.cancel_task(db, task)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return task


# ── GET /health ────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return JSONResponse({"status": "ok"})