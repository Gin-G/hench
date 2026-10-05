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
    in_plan: bool = True
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
    pay_from_account_id: str | None = None
    apr_override: Decimal | None = None
    promo_apr: Decimal | None = None
    promo_ends_on: date | None = None
    promo_balance: Decimal | None = None
    promo_deferred_interest: bool = False
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
    """Partial update: only the fields sent are changed."""

    # Blank or null clears the nickname back to the institution's name.
    nickname: str | None = None
    # False leaves the account out of the plan (see models.Account.in_plan).
    in_plan: bool | None = None
    # Credit and loan accounts only: the bank account that pays them. Null
    # goes back to checking.
    pay_from_account_id: str | None = None
    # Credit and loan accounts only. Null clears each.
    apr_override: Decimal | None = Field(default=None, ge=0, le=100)
    promo_apr: Decimal | None = Field(default=None, ge=0, le=100)
    promo_ends_on: date | None = None
    promo_balance: Decimal | None = Field(default=None, ge=0)
    promo_deferred_interest: bool | None = None


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
    pay_from_account_id: str | None = None
    balance: Decimal | None = Field(default=None, ge=0)
    apr: Decimal | None = Field(default=None, ge=0, le=100)
    promo_apr: Decimal | None = Field(default=None, ge=0, le=100)
    promo_ends_on: date | None = None
    promo_balance: Decimal | None = Field(default=None, ge=0)
    promo_deferred_interest: bool = False
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
    pay_from_account_id: str | None = None
    balance: Decimal | None = Field(default=None, ge=0)
    apr: Decimal | None = Field(default=None, ge=0, le=100)
    promo_apr: Decimal | None = Field(default=None, ge=0, le=100)
    promo_ends_on: date | None = None
    promo_balance: Decimal | None = Field(default=None, ge=0)
    promo_deferred_interest: bool | None = None
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
    pay_from_account_id: str | None = None
    balance: Decimal | None
    apr: Decimal | None
    promo_apr: Decimal | None = None
    promo_ends_on: date | None = None
    promo_balance: Decimal | None = None
    promo_deferred_interest: bool = False
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
    # A card already paid this cycle — Plaid keeps showing the old due date
    # until the next statement closes. Known from payments posted to the card
    # since its statement (pending included) covering the minimum, or from the
    # bank's own last-payment date where it reports one.
    paid: bool = False
    # What has been paid toward this cycle so far, and when the latest payment
    # posted, as seen in the card's transactions. Set for cards only.
    paid_amount: Decimal | None = None
    paid_date: date | None = None
    account: str | None = None
    # The due date is projected, not stated by the biller.
    estimated: bool = False
    # "cash" leaves a bank account on the due date; "card" is charged to a
    # credit card and so is paid through that card's own payment.
    pay_from: Literal["cash", "card"] = "cash"
    # The account the money leaves: the stream's own account for a detected
    # payment, the user's choice for a bill or a card. Null means checking.
    pay_from_account_id: str | None = None
    pay_from_name: str | None = None
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
    # The rate an extra dollar paid today saves: the promo rate while a promo
    # covers the whole balance, the regular rate otherwise. What the list is
    # ranked by.
    apr: Decimal | None = None
    # The regular rate: the user's override, else Plaid's, else the bill's.
    base_apr: Decimal | None = None
    apr_overridden: bool = False
    promo_apr: Decimal | None = None
    promo_ends_on: date | None = None
    promo_balance: Decimal | None = None
    promo_deferred_interest: bool = False
    # The promo has not ended yet.
    promo_active: bool = False
    # Monthly payment that clears the promo balance by the end date, and
    # whether the minimum alone does.
    promo_monthly_needed: Decimal | None = None
    promo_on_track: bool | None = None
    # Plaid's APR breakdown for a card, e.g. a 0% balance transfer on part of
    # the balance: a hint for filling in the promo.
    plaid_aprs: list[dict] = []
    minimum_payment: Decimal | None = None
    next_due_date: date | None = None
    statement_balance: Decimal | None = None
    is_overdue: bool = False


class DebtsResponse(BaseModel):
    # Highest current rate first.
    debts: list[DebtOut]
    total_balance: Decimal
    total_minimum: Decimal
    # Where extra money should go now, and why.
    target: DebtOut | None = None
    target_reason: str | None = None


class CashAccount(BaseModel):
    account_id: str
    name: str
    available: Decimal


class Paycheck(BaseModel):
    name: str
    date: date
    amount: Decimal
    account: str | None = None
    account_id: str | None = None
    stream_id: str | None = None


class FundingAccount(BaseModel):
    """A bank account other than checking that pays some of the bills.

    Projected on its own: a mortgage drawn from savings is covered by
    savings, not checking. Money already sitting in it is not counted as
    spare for debt — only a shortfall carries over, as a transfer checking
    has to make.
    """

    account_id: str
    name: str
    available: Decimal
    due_total: Decimal
    due_count: int
    low_point: Decimal
    low_point_date: date


class PlanResponse(BaseModel):
    """Cash now, less what is due before the next paycheck."""

    as_of: date
    cash_accounts: list[CashAccount]
    cash_total: Decimal
    next_paycheck: Paycheck | None
    # Every projected paycheck over the next few weeks, for marking paydays.
    paychecks: list[Paycheck]
    # Recurring transfers into a bank account over the same weeks, from
    # another of the user's accounts or from outside (e.g. Venmo).
    transfers_in: list[Paycheck] = []
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
    # Savings and other bank accounts that bills are drawn from, each
    # projected over the same lookahead. A negative low point there is money
    # checking must move over, and comes out of safe_extra.
    funding_accounts: list[FundingAccount] = []
    # Where extra money does the most; see DebtsResponse.target.
    target_debt: DebtOut | None
    target_reason: str | None = None


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


# --- Ledger ------------------------------------------------------------------
class LedgerAccount(BaseModel):
    """One running balance on the ledger.

    ``cash`` is money held (checking, pooled, or another bank account);
    ``debt`` is owed (a card, a loan, or a bill carrying a balance), so it
    goes down as payments reach it and up as charges land on a card.
    """

    key: str
    name: str
    kind: Literal["cash", "debt"]
    start_balance: Decimal
    end_balance: Decimal
    # Cash accounts: the lowest the balance reaches, and when.
    low_point: Decimal | None = None
    low_point_date: date | None = None


class LedgerEntry(BaseModel):
    date: date
    kind: Literal["payment", "paycheck", "transfer"]
    name: str
    amount: Decimal | None = None
    # Exactly one of these, by kind.
    payment: UpcomingPayment | None = None
    inflow: Paycheck | None = None
    # The account the money leaves (or the card it is charged to), and its
    # balance straight before and straight after. An entry that moves nothing
    # (already paid, amount unknown) shows the same balance on both sides;
    # both are null only for an account with no balance to track.
    from_key: str | None = None
    from_name: str | None = None
    from_before: Decimal | None = None
    from_balance: Decimal | None = None
    # The account the money reaches: the card or loan being paid down, or the
    # bank account a paycheck or transfer lands in.
    to_key: str | None = None
    to_name: str | None = None
    to_before: Decimal | None = None
    to_balance: Decimal | None = None


class LedgerResponse(BaseModel):
    start: date
    end: date
    accounts: list[LedgerAccount]
    entries: list[LedgerEntry]
    paychecks: list[Paycheck]
    transfers_in: list[Paycheck]
