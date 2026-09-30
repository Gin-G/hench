"""What is left until the next paycheck, and where the extra should go.

    cash in checking
  - every payment due from checking on or before the next paycheck (bills,
    card minimums, detected outflows)
  = left over

Detected payments charged to a credit card are left out of the sum: they are
paid through that card's own payment, which is already counted as the card's
minimum. Whatever is left over goes to the debt services.bills names as the
target: the highest current rate, unless a deferred-interest promo is about
to run out.

That window alone overstates what is spare when a large bill lands just after
a small paycheck, so the amount actually recommended for debt is the *low
point*: the lowest the checking balance is projected to reach over the next
``_LOOKAHEAD_DAYS``, walking every bill, paycheck and transfer in date order. Paying that
much extra today still leaves every later bill covered.

Payments are charged to the account they are drawn from. Detected payments
use the account Plaid saw them leave; bills and card or loan payments use the
account picked for them in the UI, defaulting to checking. Checking accounts
are pooled; a savings account paying the mortgage is projected on its own, and
only its shortfall, if any, comes out of what checking can spare. The running
balances themselves come from services.ledger, which the Upcoming table shows
row by row.

A recurring transfer is counted on both sides: out of the account it leaves
(a detected outflow like any other) and into the one it reaches, so moving
money from checking to savings nets to zero. A transfer in from outside, such
as Venmo, is money in. The balances already reflect past transfers, so only
recurring ones ahead matter here.

Paydays come from Plaid's recurring inflow streams. Payments due *on* a payday
are counted before that day's paycheck, since a deposit landing that morning
is not a safe thing to lean on.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account
from ..schemas import CashAccount, FundingAccount, PlanResponse
from .bills import ZERO, account_label, build_debts
from .ledger import CHECKING, balance_of, build_ledger

# How far ahead to look for a payday before giving up on finding one.
_PAYDAY_HORIZON_DAYS = 45
# How far the low-point projection runs: about two biweekly pay cycles.
_LOOKAHEAD_DAYS = 30


async def build_plan(session: AsyncSession, today: date) -> PlanResponse:
    ledger = await build_ledger(
        session, today, max(_PAYDAY_HORIZON_DAYS, _LOOKAHEAD_DAYS)
    )
    books = {a.key: a for a in ledger.accounts}
    accounts = {
        a.account_id: a for a in (await session.scalars(select(Account))).all()
    }
    cash = [
        CashAccount(account_id=a.account_id, name=account_label(a), available=balance_of(a))
        for a in accounts.values()
        if a.type == "depository" and a.subtype == "checking" and balance_of(a) is not None
    ]
    cash_total = books[CHECKING].start_balance

    def in_checking(key: str | None) -> bool:
        return key == CHECKING

    checking_paychecks = [
        e.inflow for e in ledger.entries if e.kind == "paycheck" and in_checking(e.to_key)
    ]
    next_paycheck = checking_paychecks[0] if checking_paychecks else None
    # Without a known payday, fall back to a fortnight — about the longest
    # gap between the biweekly paychecks this is built around.
    until = next_paycheck.date if next_paycheck else today + timedelta(days=14)
    horizon = today + timedelta(days=_LOOKAHEAD_DAYS)

    # Unpaid payments leaving a bank account; card charges are inside the
    # card's own payment.
    payments = [
        e
        for e in ledger.entries
        if e.payment and not e.payment.paid and e.payment.pay_from == "cash"
    ]
    due = [e for e in payments if in_checking(e.from_key) and e.date <= until]
    due_total = sum((e.amount for e in due if e.amount is not None), ZERO)

    def low_point(key: str) -> tuple[Decimal, date]:
        # The ledger's running balances, cut at the lookahead.
        low, low_date = books[key].start_balance, today
        for e in ledger.entries:
            if e.date > horizon:
                break
            for k, bal in ((e.from_key, e.from_balance), (e.to_key, e.to_balance)):
                if k == key and bal is not None and bal < low:
                    low, low_date = bal, e.date
        return low, low_date

    low, low_date = low_point(CHECKING)

    funding = []
    for book in ledger.accounts:
        if book.kind != "cash" or book.key == CHECKING:
            continue
        # Listed for what it pays; money only arriving there is not a concern.
        drawn = [
            e
            for e in payments
            if e.from_key == book.key and e.amount is not None and e.date <= horizon
        ]
        if not drawn:
            continue
        a_low, a_low_date = low_point(book.key)
        funding.append(
            FundingAccount(
                account_id=book.key,
                name=book.name,
                available=book.start_balance,
                due_total=sum((e.amount for e in drawn), ZERO),
                due_count=len(drawn),
                low_point=a_low,
                low_point_date=a_low_date,
            )
        )
    # Whatever another account will come up short is a transfer checking has
    # to make, so it is not spare.
    shortfall = sum((-f.low_point for f in funding if f.low_point < 0), ZERO)

    debts = await build_debts(session, today)

    return PlanResponse(
        as_of=today,
        cash_accounts=cash,
        cash_total=cash_total,
        next_paycheck=next_paycheck,
        paychecks=ledger.paychecks,
        transfers_in=ledger.transfers_in,
        until=until,
        due_total=due_total,
        due_count=len(due),
        unknown_amounts=sum(1 for e in due if e.amount is None),
        left_over=cash_total - due_total,
        horizon=horizon,
        low_point=low,
        low_point_date=low_date,
        safe_extra=max(low - shortfall, ZERO),
        funding_accounts=funding,
        target_debt=debts.target,
        target_reason=debts.target_reason,
    )
