"""SQLAlchemy ORM models.

Plaid identifiers are used as natural primary keys where stable
(``item_id``, ``account_id``, ``transaction_id``) so syncs are idempotent.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Money is stored as NUMERIC and therefore comes back as Decimal, never float.
Money = Numeric(14, 2)
# Rates are percentages, e.g. 24.99 for 24.99% APR.
Rate = Numeric(7, 4)


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

    # --- Balances, refreshed from /accounts/balance/get on each sync --------
    # Plaid's sign convention differs by account type: for depository accounts
    # `current` is what you have, for credit accounts it is what you *owe*
    # (positive = outstanding debt). `available` is null on many credit cards.
    current_balance: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    available_balance: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    # Credit accounts only; null for depository.
    credit_limit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    iso_currency_code: Mapped[str | None] = mapped_column(String, nullable=True)
    balances_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    item: Mapped[Item] = relationship(back_populates="accounts")
    liability: Mapped["Liability | None"] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )


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


class Liability(Base):
    """Credit card / student loan / mortgage detail from /liabilities/get.

    One row per account that Plaid reports a liability for, so this is keyed on
    ``account_id`` rather than carrying its own id. Plaid returns three
    differently-shaped payloads (credit, student, mortgage); rather than three
    tables for a single-user app this is their union with a ``liability_type``
    discriminator, and everything unmodelled is kept in ``raw`` so a forecast
    can reach for it later without another migration.

    This is what makes "pay more than the minimum" answerable: APR, minimum
    payment, statement balance and due date all arrive here.
    """

    __tablename__ = "liabilities"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), primary_key=True
    )
    # "credit" | "student" | "mortgage"
    liability_type: Mapped[str] = mapped_column(String, nullable=False)

    # --- Common across all three types -------------------------------------
    next_payment_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    minimum_payment_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    last_payment_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    last_payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_overdue: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Credit cards ------------------------------------------------------
    last_statement_balance: Mapped[Decimal | None] = mapped_column(
        Money, nullable=True
    )
    last_statement_issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Plaid returns a list of APRs per card (purchase, cash advance, balance
    # transfer, special). The full list lives in `aprs`; this is the purchase
    # APR pulled out, since that is the one payoff maths wants.
    purchase_apr: Mapped[Decimal | None] = mapped_column(Rate, nullable=True)
    aprs: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Student loans and mortgages ---------------------------------------
    interest_rate_percentage: Mapped[Decimal | None] = mapped_column(
        Rate, nullable=True
    )
    origination_principal_amount: Mapped[Decimal | None] = mapped_column(
        Money, nullable=True
    )
    origination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    outstanding_interest_amount: Mapped[Decimal | None] = mapped_column(
        Money, nullable=True
    )
    expected_payoff_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    loan_status: Mapped[str | None] = mapped_column(String, nullable=True)

    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(back_populates="liability")


class RecurringStream(Base):
    """A detected recurring inflow or outflow from /transactions/recurring/get.

    Inflow streams are where paydays come from; outflow streams are bills and
    subscriptions that never appear in /liabilities/get because they are not
    debt. Plaid needs roughly 90 days of history and at least two occurrences
    before it will detect a stream, so this table stays empty for a while
    after linking.
    """

    __tablename__ = "recurring_streams"

    stream_id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.item_id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "inflow" (income) | "outflow" (bills, subscriptions)
    direction: Mapped[str] = mapped_column(String, index=True, nullable=False)

    description: Mapped[str | None] = mapped_column(String, nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # WEEKLY | BIWEEKLY | SEMI_MONTHLY | MONTHLY | ANNUALLY | UNKNOWN
    frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    # Plaid amounts here follow the transaction convention: positive = money
    # out. Inflow streams therefore arrive negative.
    average_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    last_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    iso_currency_code: Mapped[str | None] = mapped_column(String, nullable=True)

    first_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    predicted_next_date: Mapped[date | None] = mapped_column(
        Date, index=True, nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # MATURE | EARLY_DETECTION | TOMBSTONED | UNKNOWN
    status: Mapped[str | None] = mapped_column(String, nullable=True)

    category_primary: Mapped[str | None] = mapped_column(String, nullable=True)
    category_detailed: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


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
