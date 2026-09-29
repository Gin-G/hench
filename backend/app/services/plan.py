"""What is left until the next paycheck, and where the extra should go.

    cash in checking
  - every payment due from checking on or before the next paycheck (bills,
    card minimums, detected outflows)
  = left over

Detected payments charged to a credit card are left out of the sum: they are
paid through that card's own payment, which is already counted as the card's
minimum. Whatever is left over is best put toward the debt with the highest
APR, which is named as the target.

That window alone overstates what is spare when a large bill lands just after
a small paycheck, so the amount actually recommended for debt is the *low
point*: the lowest the checking balance is projected to reach over the next
``_LOOKAHEAD_DAYS``, walking every bill, paycheck and transfer in date order. Paying that
much extra today still leaves every later bill covered.

Payments are charged to the account they are drawn from. Detected payments
use the account Plaid saw them leave; bills and card or loan payments use the
account picked for them in the UI, defaulting to checking. Checking accounts
are pooled; a savings account paying the mortgage is projected on its own, and
only its shortfall, if any, comes out of what checking can spare.

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

from ..models import Account, RecurringStream
from ..schemas import (
    CashAccount,
    FundingAccount,
    Paycheck,
    PlanResponse,
    UpcomingPayment,
)
from .bills import ZERO, account_label, build_debts, build_upcoming, clean_name
from .schedule import PLAID_FREQUENCIES, occurrences

# How far ahead to look for a payday before giving up on finding one.
_PAYDAY_HORIZON_DAYS = 45
# How far the low-point projection runs: about two biweekly pay cycles.
_LOOKAHEAD_DAYS = 30


def _is_paycheck(stream: RecurringStream) -> bool:
    # INCOME_WAGES is Plaid's detailed category for payroll; the description
    # check catches payroll Plaid filed elsewhere. Interest, refunds and
    # transfers in are not paychecks.
    return stream.category_detailed == "INCOME_WAGES" or "payroll" in (
        stream.description or ""
    ).lower()


def _is_transfer_in(stream: RecurringStream, accounts: dict[str, Account]) -> bool:
    # Money moved into a bank account: from another of the user's accounts
    # (the matching outflow is already counted on that side, so the pair nets
    # to zero) or from outside, like a Venmo cash-out, which is real money in.
    account = accounts.get(stream.account_id)
    return (
        stream.category_primary == "TRANSFER_IN"
        and account is not None
        and account.type == "depository"
    )


async def _inflows(
    session: AsyncSession, accounts: dict[str, Account], today: date
) -> tuple[list[Paycheck], list[Paycheck]]:
    """Projected paychecks, and projected transfers in, each by date."""
    streams = (
        await session.scalars(
            select(RecurringStream).where(
                RecurringStream.direction == "inflow",
                RecurringStream.is_active.is_(True),
                RecurringStream.hidden.is_(False),
                RecurringStream.predicted_next_date.is_not(None),
            )
        )
    ).all()
    end = today + timedelta(days=_PAYDAY_HORIZON_DAYS)
    paychecks, transfers = [], []
    for stream in streams:
        if stream.average_amount is None:
            continue
        if _is_paycheck(stream):
            into, fallback = paychecks, "Paycheck"
        elif _is_transfer_in(stream, accounts):
            into, fallback = transfers, "Transfer in"
        else:
            continue
        frequency = PLAID_FREQUENCIES.get(stream.frequency or "", "once")
        if stream.frequency == "SEMI_MONTHLY":
            frequency = "semi_monthly"
        account = accounts.get(stream.account_id)
        for on in occurrences(stream.predicted_next_date, frequency, end):
            if on < today:
                continue
            into.append(
                Paycheck(
                    name=clean_name(stream.merchant_name or stream.description or fallback),
                    date=on,
                    # Inflows arrive negative under Plaid's sign convention.
                    amount=abs(stream.average_amount),
                    account=account_label(account) if account else None,
                    account_id=stream.account_id,
                    stream_id=stream.stream_id,
                )
            )
    return (
        sorted(paychecks, key=lambda p: p.date),
        sorted(transfers, key=lambda p: p.date),
    )


def _low_point(
    start: Decimal,
    outflows: list[UpcomingPayment],
    inflows: list[Paycheck],
    today: date,
    horizon: date,
) -> tuple[Decimal, date]:
    """Walk the lookahead in date order, bills before money in on the same
    day, and return the lowest balance reached and when."""
    events = [(p.due_date, 0, -p.amount) for p in outflows if p.due_date <= horizon]
    events += [(c.date, 1, c.amount) for c in inflows if c.date <= horizon]
    running = low = start
    low_date = today
    for on, _, delta in sorted(events, key=lambda e: (e[0], e[1])):
        running += delta
        if running < low:
            low, low_date = running, on
    return low, low_date


def _balance(account: Account) -> Decimal | None:
    # Available, not current: a pending debit is already spoken for.
    if account.available_balance is not None:
        return account.available_balance
    return account.current_balance


async def build_plan(session: AsyncSession, today: date) -> PlanResponse:
    accounts = {
        a.account_id: a for a in (await session.scalars(select(Account))).all()
    }
    banked = {
        a.account_id: a
        for a in accounts.values()
        if a.type == "depository" and _balance(a) is not None
    }
    # Every checking account is pooled as one: that is where spending and
    # extra debt payments come from. Savings and the like each stand alone.
    cash = [
        CashAccount(account_id=a.account_id, name=account_label(a), available=_balance(a))
        for a in banked.values()
        if a.subtype == "checking"
    ]
    others = {i: a for i, a in banked.items() if a.subtype != "checking"}
    cash_total = sum((c.available for c in cash), ZERO)

    def pool_of(account_id: str | None) -> str | None:
        # None is checking: unassigned payments, checking itself, and any
        # account without a balance to project.
        return account_id if account_id in others else None

    paychecks, transfers_in = await _inflows(session, accounts, today)
    checking_paychecks = [c for c in paychecks if pool_of(c.account_id) is None]
    next_paycheck = checking_paychecks[0] if checking_paychecks else None
    # Without a known payday, fall back to a fortnight — about the longest
    # gap between the biweekly paychecks this is built around.
    until = next_paycheck.date if next_paycheck else today + timedelta(days=14)

    horizon = today + timedelta(days=_LOOKAHEAD_DAYS)
    upcoming = await build_upcoming(
        session, today, (max(until, horizon) - today).days
    )
    unpaid = [p for p in upcoming.payments if p.pay_from == "cash" and not p.paid]
    outflows = [p for p in unpaid if p.amount is not None]
    due = [
        p
        for p in unpaid
        if p.due_date <= until and pool_of(p.pay_from_account_id) is None
    ]
    due_total = sum((p.amount for p in due if p.amount is not None), ZERO)

    low, low_date = _low_point(
        cash_total,
        [p for p in outflows if pool_of(p.pay_from_account_id) is None],
        checking_paychecks
        + [t for t in transfers_in if pool_of(t.account_id) is None],
        today,
        horizon,
    )

    funding = []
    for account_id, account in others.items():
        drawn = [p for p in outflows if p.pay_from_account_id == account_id]
        # Listed for what it pays; money only arriving there is not a concern.
        if not drawn:
            continue
        paid_in = [c for c in paychecks + transfers_in if c.account_id == account_id]
        in_window = [p for p in drawn if p.due_date <= horizon]
        a_low, a_low_date = _low_point(
            _balance(account), drawn, paid_in, today, horizon
        )
        funding.append(
            FundingAccount(
                account_id=account_id,
                name=account_label(account),
                available=_balance(account),
                due_total=sum((p.amount for p in in_window), ZERO),
                due_count=len(in_window),
                low_point=a_low,
                low_point_date=a_low_date,
            )
        )
    # Whatever another account will come up short is a transfer checking has
    # to make, so it is not spare.
    shortfall = sum((-f.low_point for f in funding if f.low_point < 0), ZERO)

    debts = await build_debts(session, today)
    with_rate = [d for d in debts.debts if d.apr is not None and d.balance > 0]
    target = max(with_rate, key=lambda d: d.apr) if with_rate else None

    return PlanResponse(
        as_of=today,
        cash_accounts=cash,
        cash_total=cash_total,
        next_paycheck=next_paycheck,
        paychecks=paychecks,
        transfers_in=transfers_in,
        until=until,
        due_total=due_total,
        due_count=len(due),
        unknown_amounts=sum(1 for p in due if p.amount is None),
        left_over=cash_total - due_total,
        horizon=horizon,
        low_point=low,
        low_point_date=low_date,
        safe_extra=max(low - shortfall, ZERO),
        funding_accounts=funding,
        target_debt=target,
    )
