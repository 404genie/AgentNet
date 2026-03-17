import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    # agent_id mirrors the UUID from the agent registry — not a cross-service FK,
    # stored here for correlation. Must be unique to serve as FK target.
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True,
    )
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # balance = total credited funds (includes held amount)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"),
    )
    # held_balance = portion of balance reserved for in-flight tasks
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
    # viewonly=True — transactions are audit records; never written via ORM relationship
    transactions_sent: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        foreign_keys="[Transaction.from_agent_id]",
        back_populates="from_wallet",
        viewonly=True,
    )
    transactions_received: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        foreign_keys="[Transaction.to_agent_id]",
        back_populates="to_wallet",
        viewonly=True,
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
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # active → settled (payment transferred) or released (task failed, funds returned)
    status: Mapped[str] = mapped_column(
        Enum("active", "settled", "released", name="hold_status", create_type=False),
        nullable=False, default="active",
    )
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
    # from_agent_id is NULL for topup (no payer — admin funds the wallet)
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
    tx_type: Mapped[str] = mapped_column(
        Enum("topup", "hold", "release", "settlement", name="tx_type", create_type=False),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    from_wallet: Mapped["Wallet | None"] = relationship(
        "Wallet",
        foreign_keys="[Transaction.from_agent_id]",
        back_populates="transactions_sent",
        viewonly=True,
    )
    to_wallet: Mapped["Wallet"] = relationship(
        "Wallet",
        foreign_keys="[Transaction.to_agent_id]",
        back_populates="transactions_received",
        viewonly=True,
    )