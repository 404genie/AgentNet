import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    # agent_id mirrors the UUID from the agent registry — not a FK
    # across services, but kept for cross-service correlation.
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True,
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # balance = spendable funds (does not include held amount)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"),
    )
    # held_balance = funds reserved for in-flight tasks, not yet settled
    held_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    holds: Mapped[list["Hold"]] = relationship(
        "Hold", back_populates="wallet", cascade="all, delete-orphan",
    )
    transactions_sent: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="Transaction.from_agent_id",
        back_populates="from_wallet", cascade="all, delete-orphan",
    )
    transactions_received: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="Transaction.to_agent_id",
        back_populates="to_wallet",
    )

    @property
    def available_balance(self) -> Decimal:
        """Spendable balance — excludes funds currently held."""
        return self.balance - self.held_balance


class Hold(Base):
    __tablename__ = "holds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False,
    )
    # active → settled (payment made) or released (task failed, funds returned)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="holds")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    # from_agent_id is NULL for topup (admin → agent, no payer wallet)
    from_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.agent_id", ondelete="SET NULL"),
        nullable=True,
    )
    to_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    # task_id is NULL for topup transactions
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # topup | hold | release | settlement
    tx_type: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    from_wallet: Mapped["Wallet | None"] = relationship(
        "Wallet", foreign_keys=[from_agent_id], back_populates="transactions_sent",
    )
    to_wallet: Mapped["Wallet"] = relationship(
        "Wallet", foreign_keys=[to_agent_id], back_populates="transactions_received",
    )