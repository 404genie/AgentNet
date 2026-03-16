import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app import crud
from app.database import Base, engine, get_db, settings
from app.models import Transaction
from app.schemas import (
    CreateWalletRequest,
    HoldRequest,
    PaymentResponse,
    ReleaseRequest,
    SettleRequest,
    TopUpRequest,
    TransactionListResponse,
    WalletResponse,
)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates tables from ORM metadata — for dev convenience only.
    # For production, run migrations/init.sql for constraints, indexes,
    # and the updated_at trigger.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Payment Railway",
    description=(
        "Credit ledger for the AI Agent Economy. "
        "Manages agent wallets, holds funds before task dispatch, "
        "settles payment on completion, and releases holds on failure."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ── POST /wallets ──────────────────────────────────────────────────────────────

@app.post(
    "/wallets",
    response_model=WalletResponse,
    status_code=201,
    summary="Create a wallet for an agent",
    tags=["Wallets"],
)
async def create_wallet(
    body: CreateWalletRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Admin endpoint — create a wallet for an agent.
    The wallet is pre-funded with `STARTING_BALANCE` credits (default: 100.00).
    """
    try:
        wallet = await crud.create_wallet(db, body, settings.starting_balance)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return wallet


# ── GET /wallets/{agent_id} ────────────────────────────────────────────────────

@app.get(
    "/wallets/{agent_id}",
    response_model=WalletResponse,
    summary="Get wallet by agent ID",
    tags=["Wallets"],
)
async def get_wallet(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    wallet = await crud.get_wallet_by_agent_id(db, agent_id)
    if not wallet:
        raise HTTPException(
            status_code=404,
            detail=f"No wallet found for agent '{agent_id}'.",
        )
    return wallet


# ── POST /wallets/{agent_id}/topup ─────────────────────────────────────────────

@app.post(
    "/wallets/{agent_id}/topup",
    response_model=WalletResponse,
    summary="Top up an agent's wallet",
    tags=["Wallets"],
)
async def topup_wallet(
    agent_id: uuid.UUID,
    body: TopUpRequest,
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint — add credits to an agent's wallet."""
    wallet = await crud.get_wallet_by_agent_id(db, agent_id)
    if not wallet:
        raise HTTPException(
            status_code=404,
            detail=f"No wallet found for agent '{agent_id}'.",
        )
    wallet = await crud.topup_wallet(db, wallet, body.amount)
    return wallet


# ── POST /payments/hold ────────────────────────────────────────────────────────

@app.post(
    "/payments/hold",
    response_model=PaymentResponse,
    status_code=201,
    summary="Reserve funds before task dispatch",
    tags=["Payments"],
)
async def hold_payment(
    body: HoldRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the broker before dispatching a task.
    Reserves `amount` credits from the payer's available balance.

    Returns 402 if the payer has insufficient funds.
    Returns 409 if a hold already exists for this task_id.
    Returns 404 if either wallet doesn't exist.
    """
    # Check for duplicate hold
    existing = await crud.get_hold_by_task_id(db, body.task_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A hold already exists for task '{body.task_id}'.",
        )

    payer_wallet = await crud.get_wallet_by_agent_id(db, body.payer_agent_id)
    if not payer_wallet:
        raise HTTPException(
            status_code=404,
            detail=f"No wallet found for payer agent '{body.payer_agent_id}'.",
        )

    payee_wallet = await crud.get_wallet_by_agent_id(db, body.payee_agent_id)
    if not payee_wallet:
        raise HTTPException(
            status_code=404,
            detail=f"No wallet found for payee agent '{body.payee_agent_id}'.",
        )

    # Pre-flight balance check
    if payer_wallet.available_balance < body.amount:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient funds. "
                f"Required: {body.amount}, "
                f"Available: {payer_wallet.available_balance}."
            ),
        )

    await crud.create_hold(
        db, payer_wallet, body.payee_agent_id, body.task_id, body.amount,
    )

    return PaymentResponse(
        task_id=body.task_id,
        status="held",
        amount=body.amount,
        payer_agent_id=body.payer_agent_id,
        payee_agent_id=body.payee_agent_id,
        message=f"{body.amount} credits reserved for task.",
    )


# ── POST /payments/settle ──────────────────────────────────────────────────────

@app.post(
    "/payments/settle",
    response_model=PaymentResponse,
    summary="Settle payment on task completion",
    tags=["Payments"],
)
async def settle_payment(
    body: SettleRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the broker when a task completes successfully.
    Transfers held funds from payer to payee and closes the hold.
    """
    hold = await crud.get_hold_by_task_id(db, body.task_id)
    if not hold:
        raise HTTPException(
            status_code=404,
            detail=f"No hold found for task '{body.task_id}'.",
        )
    if hold.status != "active":
        raise HTTPException(
            status_code=409,
            detail=f"Hold for task '{body.task_id}' is already '{hold.status}'.",
        )

    # Load payer wallet via wallet_id — avoids async lazy-load on hold.wallet
    payer_wallet = await crud.get_wallet_by_id(db, hold.wallet_id)
    if not payer_wallet:
        raise HTTPException(status_code=404, detail="Payer wallet not found.")

    # Find the payee from the hold's transaction record
    tx_result = await db.execute(
        select(Transaction).where(
            Transaction.task_id == body.task_id,
            Transaction.tx_type == "hold",
        )
    )
    hold_tx = tx_result.scalar_one_or_none()
    if not hold_tx:
        raise HTTPException(status_code=404, detail="Hold transaction record not found.")

    payee_wallet = await crud.get_wallet_by_agent_id(db, hold_tx.to_agent_id)
    if not payee_wallet:
        raise HTTPException(status_code=404, detail="Payee wallet not found.")

    await crud.settle_hold(db, hold, payer_wallet, payee_wallet)

    return PaymentResponse(
        task_id=body.task_id,
        status="settled",
        amount=hold.amount,
        payer_agent_id=payer_wallet.agent_id,
        payee_agent_id=payee_wallet.agent_id,
        message=f"{hold.amount} credits transferred to payee.",
    )


# ── POST /payments/release ─────────────────────────────────────────────────────

@app.post(
    "/payments/release",
    response_model=PaymentResponse,
    summary="Release hold on task failure",
    tags=["Payments"],
)
async def release_payment(
    body: ReleaseRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the broker when a task fails.
    Returns held funds to the payer's available balance.
    """
    hold = await crud.get_hold_by_task_id(db, body.task_id)
    if not hold:
        raise HTTPException(
            status_code=404,
            detail=f"No hold found for task '{body.task_id}'.",
        )
    if hold.status != "active":
        raise HTTPException(
            status_code=409,
            detail=f"Hold for task '{body.task_id}' is already '{hold.status}'.",
        )

    # Load payer wallet explicitly — avoids async lazy-load on hold.wallet
    payer_wallet = await crud.get_wallet_by_id(db, hold.wallet_id)
    if not payer_wallet:
        raise HTTPException(status_code=404, detail="Payer wallet not found.")

    # Get payee from original hold transaction
    tx_result = await db.execute(
        select(Transaction).where(
            Transaction.task_id == body.task_id,
            Transaction.tx_type == "hold",
        )
    )
    hold_tx = tx_result.scalar_one_or_none()
    payee_agent_id = hold_tx.to_agent_id if hold_tx else payer_wallet.agent_id

    await crud.release_hold(db, hold, payer_wallet, payee_agent_id)

    return PaymentResponse(
        task_id=body.task_id,
        status="released",
        amount=hold.amount,
        payer_agent_id=payer_wallet.agent_id,
        payee_agent_id=payee_agent_id,
        message=f"{hold.amount} credits returned to payer.",
    )


# ── GET /payments/transactions ─────────────────────────────────────────────────

@app.get(
    "/payments/transactions",
    response_model=TransactionListResponse,
    summary="List transactions",
    tags=["Payments"],
)
async def list_transactions(
    agent_id: uuid.UUID | None = Query(default=None),
    task_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Audit log of all payment transactions.
    Filter by agent_id (shows both sent and received) or task_id.
    """
    txs, total = await crud.list_transactions(db, agent_id, task_id, limit, offset)
    return TransactionListResponse(transactions=txs, total=total)


# ── GET /health ────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return JSONResponse({"status": "ok"})