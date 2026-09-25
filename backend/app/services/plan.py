"""What is left until the next paycheck, and where the extra should go.

    cash in checking
  - every payment due on or before the next paycheck that leaves a bank
    account (bills, card minimums, detected outflows paid from checking)
  = left over

Detected payments charged to a credit card are left out of the sum: they are
paid through that card's own payment, which is already counted as the card's
minimum. Whatever is left over is best put toward the debt with the highest
APR, which is named as the target.

That window alone overstates what is spare when a large bill lands just after
a small paycheck, so the amount actually recommended for debt is the *low
point*: the lowest the checking balance is projected to reach over the next
``_LOOKAHEAD_DAYS``, walking every bill and paycheck in date order. Paying that
much extra today still leaves every later bill covered.

Paydays come from Plaid's recurring inflow streams. Payments due *on* a payday
are counted before that day's paycheck, since a deposit landing that morning
is not a safe thing to lean on.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account, RecurringStream
from ..schemas import CashAccount, Paycheck, PlanResponse
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


async def _paychecks(
    session: AsyncSession, accounts: dict[str, Account], today: date
) -> list[Paycheck]:
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
    paychecks = []
    for stream in streams:
        if not _is_paycheck(stream) or stream.average_amount is None:
            continue
        frequency = PLAID_FREQUENCIES.get(stream.frequency or "", "once")
        if stream.frequency == "SEMI_MONTHLY":
            frequency = "semi_monthly"
        for on in occurrences(stream.predicted_next_date, frequency, end):
            if on < today:
                continue
            account = accounts.get(stream.account_id)
            paychecks.append(
                Paycheck(
                    name=clean_name(stream.merchant_name or stream.description or "Paycheck"),
                    date=on,
                    # Inflows arrive negative under Plaid's sign convention.
                    amount=abs(stream.average_amount),
                    account=account_label(account) if account else None,
                )
            )
    return sorted(paychecks, key=lambda p: p.date)


async def build_plan(session: AsyncSession, today: date) -> PlanResponse:
    accounts = {
        a.account_id: a for a in (await session.scalars(select(Account))).all()
    }
    cash = [
        CashAccount(
            account_id=a.account_id,
            name=account_label(a),
            # Available, not current: a pending debit is already spoken for.
            available=a.available_balance if a.available_balance is not None else a.current_balance,
        )
        for a in accounts.values()
        if a.type == "depository"
        and a.subtype == "checking"
        and (a.available_balance is not None or a.current_balance is not None)
    ]
    cash_total = sum((c.available for c in cash), ZERO)

    paychecks = await _paychecks(session, accounts, today)
    next_paycheck = paychecks[0] if paychecks else None
    # Without a known payday, fall back to a fortnight — about the longest
    # gap between the biweekly paychecks this is built around.
    until = next_paycheck.date if next_paycheck else today + timedelta(days=14)

    horizon = today + timedelta(days=_LOOKAHEAD_DAYS)
    upcoming = await build_upcoming(
        session, today, (max(until, horizon) - today).days
    )
    outflows = [
        p
        for p in upcoming.payments
        if p.pay_from == "cash" and not p.paid and p.amount is not None
    ]
    due = [
        p
        for p in upcoming.payments
        if p.pay_from == "cash" and not p.paid and p.due_date <= until
    ]
    due_total = sum((p.amount for p in due if p.amount is not None), ZERO)

    # Walk the lookahead in date order, bills before paychecks on the same
    # day, tracking the lowest balance reached.
    events = [(p.due_date, 0, -p.amount) for p in outflows if p.due_date <= horizon]
    events += [(c.date, 1, c.amount) for c in paychecks if c.date <= horizon]
    running = low = cash_total
    low_date = today
    for on, _, delta in sorted(events, key=lambda e: (e[0], e[1])):
        running += delta
        if running < low:
            low, low_date = running, on

    debts = await build_debts(session, today)
    with_rate = [d for d in debts.debts if d.apr is not None and d.balance > 0]
    target = max(with_rate, key=lambda d: d.apr) if with_rate else None

    return PlanResponse(
        as_of=today,
        cash_accounts=cash,
        cash_total=cash_total,
        next_paycheck=next_paycheck,
        paychecks=paychecks,
        until=until,
        due_total=due_total,
        due_count=len(due),
        unknown_amounts=sum(1 for p in due if p.amount is None),
        left_over=cash_total - due_total,
        horizon=horizon,
        low_point=low,
        low_point_date=low_date,
        safe_extra=max(low, ZERO),
        target_debt=target,
    )
