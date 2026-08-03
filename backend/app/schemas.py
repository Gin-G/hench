"""Pydantic request/response models for the frontend API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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
    status: str | None = None
    category_primary: str | None = None
    category_detailed: str | None = None


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
