"""Pydantic request/response models for the frontend API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Link ------------------------------------------------------------------
class CreateLinkTokenRequest(BaseModel):
    # When set, a link token for *update mode* (re-auth) is created for the
    # given Item instead of a brand-new link.
    item_id: str | None = None


class CreateLinkTokenResponse(BaseModel):
    link_token: str
    expiration: str | None = None


class ExchangePublicTokenRequest(BaseModel):
    public_token: str


class ExchangePublicTokenResponse(BaseModel):
    item_id: str
    institution_name: str | None = None


# --- Items -----------------------------------------------------------------
class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: str
    institution_name: str | None
    status: str
    last_synced_at: datetime | None


# --- Accounts, balances, liabilities ---------------------------------------
# Money on these stays Decimal rather than float, because these feed payoff and
# allocation maths rather than a chart. Pydantic v2 serialises Decimal to a
# JSON *string* ("41.00", not 41.0), which avoids a binary-float round trip
# entirely — but means the frontend must parse rather than assume a number.
# The older TransactionOut/SankeyResponse fields stay float deliberately, so
# nothing currently rendering a chart changes shape.
class LiabilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    liability_type: str
    next_payment_due_date: date | None = None
    minimum_payment_amount: Decimal | None = None
    last_payment_amount: Decimal | None = None
    last_payment_date: date | None = None
    is_overdue: bool | None = None
    last_statement_balance: Decimal | None = None
    last_statement_issue_date: date | None = None
    purchase_apr: Decimal | None = None
    interest_rate_percentage: Decimal | None = None
    origination_principal_amount: Decimal | None = None
    expected_payoff_date: date | None = None
    loan_status: str | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: str
    item_id: str
    name: str | None = None
    nickname: str | None = None
    official_name: str | None = None
    mask: str | None = None
    type: str | None = None
    subtype: str | None = None
    current_balance: Decimal | None = None
    available_balance: Decimal | None = None
    credit_limit: Decimal | None = None
    iso_currency_code: str | None = None
    balances_updated_at: datetime | None = None
    institution_name: str | None = None
    liability: LiabilityOut | None = None


class RecurringStreamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stream_id: str
    account_id: str
    direction: str
    description: str | None = None
    merchant_name: str | None = None
    frequency: str | None = None
    average_amount: Decimal | None = None
    last_amount: Decimal | None = None
    first_date: date | None = None
    last_date: date | None = None
    predicted_next_date: date | None = None
    is_active: bool
    hidden: bool
    status: str | None = None
    category_primary: str | None = None
    category_detailed: str | None = None


class AccountUpdate(BaseModel):
    # Blank or null clears the nickname back to the institution's name.
    nickname: str | None = None


class RecurringStreamUpdate(BaseModel):
    hidden: bool


# --- Bills and debts -------------------------------------------------------
# Money stays Decimal (a JSON string) here too, for the same reason as above.
BillFrequency = Literal["once", "weekly", "biweekly", "monthly", "quarterly", "annually"]


class BillIn(BaseModel):
    name: str = Field(min_length=1)
    amount: Decimal | None = Field(default=None, ge=0)
    frequency: BillFrequency = "monthly"
    next_due_date: date
    autopay: bool = False
    category: str | None = None
    notes: str | None = None
    balance: Decimal | None = Field(default=None, ge=0)
    apr: Decimal | None = Field(default=None, ge=0, le=100)
    active: bool = True


class BillUpdate(BaseModel):
    """Partial update: only the fields sent are changed, and an explicit null
    clears an optional field (e.g. ``balance: null`` stops a bill being a debt).
    """

    name: str | None = Field(default=None, min_length=1)
    amount: Decimal | None = Field(default=None, ge=0)
    frequency: BillFrequency | None = None
    next_due_date: date | None = None
    autopay: bool | None = None
    category: str | None = None
    notes: str | None = None
    balance: Decimal | None = Field(default=None, ge=0)
    apr: Decimal | None = Field(default=None, ge=0, le=100)
    active: bool | None = None


class BillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    amount: Decimal | None
    frequency: str
    next_due_date: date
    autopay: bool
    category: str | None
    notes: str | None
    balance: Decimal | None
    apr: Decimal | None
    last_paid_date: date | None
    active: bool
    due_date_estimated: bool = False
    # Set when sync owns this bill (e.g. "longmont"); edits would be overwritten.
    source: str | None = None
    source_synced_at: datetime | None = None
    source_error: str | None = None


class MarkPaidRequest(BaseModel):
    # Defaults to today.
    paid_on: date | None = None


class UpcomingPayment(BaseModel):
    """One dated payment on the timeline, whichever source it came from."""

    # bill = entered by hand; card = Plaid liability; recurring = a stream
    # Plaid detected from transaction history.
    source: Literal["bill", "card", "recurring"]
    # Bill id, account_id or stream_id, depending on source.
    ref_id: str
    name: str
    due_date: date
    amount: Decimal | None = None
    # "minimum" for card minimums, "average" for a detected stream's typical
    # amount; null when the amount is the bill's own fixed figure.
    amount_basis: Literal["minimum", "average"] | None = None
    statement_balance: Decimal | None = None
    autopay: bool | None = None
    overdue: bool = False
    # A card whose last payment is on or after its last statement date — Plaid
    # keeps showing the old due date until the next statement closes.
    paid: bool = False
    account: str | None = None
    # The due date is projected, not stated by the biller.
    estimated: bool = False
    # "cash" leaves a bank account on the due date; "card" is charged to a
    # credit card and so is paid through that card's own payment.
    pay_from: Literal["cash", "card"] = "cash"
    # Set for a bill kept up to date by sync, e.g. "longmont".
    synced_from: str | None = None


class UpcomingResponse(BaseModel):
    start: date
    end: date
    payments: list[UpcomingPayment]
    # Sum of every unpaid payment with a known amount, overdue included, less
    # those charged to a card (counted in the card's own payment instead).
    total_due: Decimal


class DebtOut(BaseModel):
    source: Literal["plaid", "manual"]
    # account_id, or the bill id for manual debts.
    ref_id: str
    name: str
    institution: str | None = None
    # credit | student | mortgage | loan | other
    kind: str
    balance: Decimal
    credit_limit: Decimal | None = None
    apr: Decimal | None = None
    minimum_payment: Decimal | None = None
    next_due_date: date | None = None
    statement_balance: Decimal | None = None
    is_overdue: bool = False


class DebtsResponse(BaseModel):
    debts: list[DebtOut]
    total_balance: Decimal
    total_minimum: Decimal


class CashAccount(BaseModel):
    account_id: str
    name: str
    available: Decimal


class Paycheck(BaseModel):
    name: str
    date: date
    amount: Decimal
    account: str | None = None


class PlanResponse(BaseModel):
    """Cash now, less what is due before the next paycheck."""

    as_of: date
    cash_accounts: list[CashAccount]
    cash_total: Decimal
    next_paycheck: Paycheck | None
    # Every projected paycheck over the next few weeks, for marking paydays.
    paychecks: list[Paycheck]
    # Payments due on or before this date are counted: the next payday, or a
    # fortnight out when no paycheck has been detected.
    until: date
    due_total: Decimal
    due_count: int
    # Payments in the window with no known amount, so not in due_total.
    unknown_amounts: int
    left_over: Decimal
    # Lowest projected checking balance between now and ``horizon``, walking
    # every bill and paycheck in date order — and so the most that can go to
    # debt today without a later bill coming up short.
    horizon: date
    low_point: Decimal
    low_point_date: date
    safe_extra: Decimal
    # Highest-APR debt with a balance: where extra money does the most.
    target_debt: DebtOut | None


# --- Sync ------------------------------------------------------------------
class SyncResult(BaseModel):
    item_id: str
    added: int = 0
    modified: int = 0
    removed: int = 0


# --- Transactions ----------------------------------------------------------
class TransactionOut(BaseModel):
    transaction_id: str
    account_id: str
    date: date
    amount: float
    name: str | None
    merchant_name: str | None
    pending: bool
    # Effective category (override > rule/PFC).
    category_primary: str | None
    category_detailed: str | None
    # Original Plaid category, surfaced so the UI can show what was overridden.
    pfc_primary: str | None
    pfc_detailed: str | None
    is_overridden: bool


class TransactionList(BaseModel):
    total: int
    transactions: list[TransactionOut]


class OverrideRequest(BaseModel):
    category_primary: str
    category_detailed: str | None = None


# --- Sankey ----------------------------------------------------------------
class SankeyLink(BaseModel):
    source: str
    target: str
    value: float


class SankeyNode(BaseModel):
    name: str


class SankeyResponse(BaseModel):
    month: str  # "YYYY-MM"
    nodes: list[SankeyNode]
    links: list[SankeyLink]
    total_income: float
    total_spending: float
