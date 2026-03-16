import uuid
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hold, Transaction, Wallet
from app.schemas import CreateWalletRequest


# ── Wallets ────────────────────────────────────────────────────────────────────

async def create_wallet(
    db: AsyncSession,
    data: CreateWalletRequest,
    starting_balance: Decimal,
) -> Wallet:
    """
    Create a new wallet for an agent.
    Raises ValueError if a wallet already exists for this agent_id.
    """
    wallet = Wallet(
        agent_id=data.agent_id,
        agent_name=data.agent_name,
        balance=starting_balance,
        held_balance=Decimal("0.00"),
    )
    db.add(wallet)
    try:
        await db.flush()
    except IntegrityError:
        raise ValueError(
            f"Wallet already exists for agent '{data.agent_id}'."
        )

    # Record the starting balance as a topup transaction
    if starting_balance > 0:
        tx = Transaction(
            from_agent_id=None,   # admin topup has no payer
            to_agent_id=data.agent_id,
            task_id=None,
            amount=starting_balance,
            tx_type="topup",
            note="Initial wallet funding",
        )
        db.add(tx)
        await db.flush()

    await db.refresh(wallet)
    return wallet


async def get_wallet_by_agent_id(
    db: AsyncSession, agent_id: uuid.UUID
) -> Wallet | None:
    result = await db.execute(
        select(Wallet).where(Wallet.agent_id == agent_id)
    )
    return result.scalar_one_or_none()


async def get_wallet_by_id(
    db: AsyncSession, wallet_id: uuid.UUID
) -> Wallet | None:
    result = await db.execute(
        select(Wallet).where(Wallet.id == wallet_id)
    )
    return result.scalar_one_or_none()



async def topup_wallet(
    db: AsyncSession, wallet: Wallet, amount: Decimal
) -> Wallet:
    """Add credits to a wallet and record the transaction."""
    wallet.balance += amount
    tx = Transaction(
        from_agent_id=None,
        to_agent_id=wallet.agent_id,
        task_id=None,
        amount=amount,
        tx_type="topup",
        note="Admin top-up",
    )
    db.add(tx)
    await db.flush()
    await db.refresh(wallet)
    return wallet


# ── Holds ──────────────────────────────────────────────────────────────────────

async def get_hold_by_task_id(
    db: AsyncSession, task_id: uuid.UUID
) -> Hold | None:
    result = await db.execute(
        select(Hold).where(Hold.task_id == task_id)
    )
    return result.scalar_one_or_none()


async def create_hold(
    db: AsyncSession,
    payer_wallet: Wallet,
    payee_agent_id: uuid.UUID,
    task_id: uuid.UUID,
    amount: Decimal,
) -> Hold:
    """
    Reserve funds from the payer's balance.
    available_balance check must be done before calling this.
    """
    payer_wallet.held_balance += amount

    hold = Hold(
        wallet_id=payer_wallet.id,
        task_id=task_id,
        amount=amount,
        status="active",
    )
    db.add(hold)

    tx = Transaction(
        from_agent_id=payer_wallet.agent_id,
        to_agent_id=payee_agent_id,
        task_id=task_id,
        amount=amount,
        tx_type="hold",
        note="Funds held for task dispatch",
    )
    db.add(tx)
    await db.flush()
    return hold


async def settle_hold(
    db: AsyncSession,
    hold: Hold,
    payer_wallet: Wallet,
    payee_wallet: Wallet,
) -> None:
    """
    Settle a hold: deduct from payer, credit to payee, mark hold settled.
    """
    payer_wallet.balance -= hold.amount
    payer_wallet.held_balance -= hold.amount
    payee_wallet.balance += hold.amount
    hold.status = "settled"

    tx = Transaction(
        from_agent_id=payer_wallet.agent_id,
        to_agent_id=payee_wallet.agent_id,
        task_id=hold.task_id,
        amount=hold.amount,
        tx_type="settlement",
        note="Task completed — payment settled",
    )
    db.add(tx)
    await db.flush()


async def release_hold(
    db: AsyncSession,
    hold: Hold,
    payer_wallet: Wallet,
    payee_agent_id: uuid.UUID,
) -> None:
    """
    Release a hold: return held funds to payer's available balance.
    """
    payer_wallet.held_balance -= hold.amount
    hold.status = "released"

    tx = Transaction(
        from_agent_id=payer_wallet.agent_id,
        to_agent_id=payee_agent_id,
        task_id=hold.task_id,
        amount=hold.amount,
        tx_type="release",
        note="Task failed — hold released",
    )
    db.add(tx)
    await db.flush()


# ── Transactions ───────────────────────────────────────────────────────────────

async def list_transactions(
    db: AsyncSession,
    agent_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Transaction], int]:
    query = select(Transaction).order_by(Transaction.created_at.desc())

    if agent_id:
        query = query.where(
            (Transaction.from_agent_id == agent_id) |
            (Transaction.to_agent_id == agent_id)
        )
    if task_id:
        query = query.where(Transaction.task_id == task_id)

    count_query = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_query)).scalar_one()

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all()), total