"""SQLAlchemy ORM models.

Plaid identifiers are used as natural primary keys where stable
(``item_id``, ``account_id``, ``transaction_id``) so syncs are idempotent.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Item(Base):
    """A Plaid Item = one set of credentials at one institution."""

    __tablename__ = "items"

    # Plaid's item_id is the natural key.
    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    institution_id: Mapped[str | None] = mapped_column(String, nullable=True)
    institution_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Fernet-encrypted Plaid access token.
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    # /transactions/sync cursor; null until first sync completes.
    transactions_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "active" | "login_required" — drives Link update-mode re-auth.
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class Account(Base):
    __tablename__ = "accounts"

    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    official_name: Mapped[str | None] = mapped_column(String, nullable=True)
    mask: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    subtype: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    item: Mapped[Item] = relationship(back_populates="accounts")


class Transaction(Base):
    __tablename__ = "transactions"

    # Plaid transaction_id is the natural key — idempotent upserts.
    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Plaid convention: positive = money out of the account (spending),
    # negative = money in (income/deposits).
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    iso_currency_code: Mapped[str | None] = mapped_column(String, nullable=True)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    authorized_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String, nullable=True)
    pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_channel: Mapped[str | None] = mapped_column(String, nullable=True)

    # Plaid Personal Finance Category, as delivered by Plaid (may be
    # superseded by a rule on sync — see category_primary/detailed below).
    pfc_primary: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    pfc_detailed: Mapped[str | None] = mapped_column(String, nullable=True)
    pfc_confidence: Mapped[str | None] = mapped_column(String, nullable=True)

    # Effective category after rule application at sync time. Falls back to
    # the PFC values. A category_override (if present) wins over these at
    # query time.
    category_primary: Mapped[str | None] = mapped_column(
        String, index=True, nullable=True
    )
    category_detailed: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    override: Mapped["CategoryOverride | None"] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        uselist=False,
    )


class CategoryOverride(Base):
    """A manual, per-transaction category correction from the UI."""

    __tablename__ = "category_overrides"

    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.transaction_id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_primary: Mapped[str] = mapped_column(String, nullable=False)
    category_detailed: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    transaction: Mapped[Transaction] = relationship(back_populates="override")


class Rule(Base):
    """Merchant-pattern -> category mapping, applied during sync.

    ``match_type`` is "contains" (case-insensitive substring) or "regex".
    Matched against merchant_name, falling back to the transaction name.
    Lowest ``priority`` number wins when multiple rules match.
    """

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern: Mapped[str] = mapped_column(String, nullable=False)
    match_type: Mapped[str] = mapped_column(String, default="contains", nullable=False)
    category_primary: Mapped[str] = mapped_column(String, nullable=False)
    category_detailed: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
