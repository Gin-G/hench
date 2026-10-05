"""A running balance for every account across the upcoming timeline.

Each payment, paycheck and transfer in is posted in date order to the
accounts it touches, so every row carries the balance of each of them
straight before and straight after it:

* a payment leaves the bank account it is drawn from (or is charged to a
  card, raising what is owed on it);
* a card or loan payment, or a bill carrying a balance, also pays that debt
  down;
* a paycheck or transfer in lands in the bank account it is paid into.

Checking accounts are pooled as one, as in the planner; every other bank
account, card and loan keeps its own balance. Payments are posted before
money in on the same day — a deposit landing that morning is not a safe
thing to lean on. The planner reads its low points from here, so the ledger
and the plan cannot disagree.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account, Bill, RecurringStream
from ..schemas import LedgerAccount, LedgerEntry, LedgerResponse, Paycheck
from .bills import account_label, build_upcoming, clean_name
from .schedule import PLAID_FREQUENCIES, occurrences

CHECKING = "checking"
_DEBT_TYPES = {"credit", "loan"}


def balance_of(account: Account) -> Decimal | None:
    # Available, not current: a pending debit is already spoken for.
    if account.available_balance is not None:
        return account.available_balance
    return account.current_balance


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
    session: AsyncSession, accounts: dict[str, Account], today: date, end: date
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
    paychecks, transfers = [], []
    for stream in streams:
        if stream.average_amount is None:
            continue
        landing = accounts.get(stream.account_id)
        if landing is not None and not landing.in_plan:
            # Money into an account left out of the plan is not the user's.
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


class _Book:
    def __init__(self, key: str, name: str, kind: str, start: Decimal, today: date):
        self.account = LedgerAccount(
            key=key,
            name=name,
            kind=kind,
            start_balance=start,
            end_balance=start,
            low_point=start if kind == "cash" else None,
            low_point_date=today if kind == "cash" else None,
        )

    def post(self, delta: Decimal, on: date) -> Decimal:
        a = self.account
        a.end_balance += delta
        if a.kind == "cash" and a.end_balance < a.low_point:
            a.low_point, a.low_point_date = a.end_balance, on
        return a.end_balance


async def build_ledger(session: AsyncSession, today: date, days: int) -> LedgerResponse:
    accounts = {
        a.account_id: a for a in (await session.scalars(select(Account))).all()
    }
    upcoming = await build_upcoming(session, today, days)
    end = upcoming.end
    paychecks, transfers = await _inflows(session, accounts, today, end)

    books: dict[str, _Book] = {}
    checking = [
        a
        for a in accounts.values()
        if a.type == "depository"
        and a.subtype == "checking"
        and a.in_plan
        and balance_of(a) is not None
    ]
    books[CHECKING] = _Book(
        CHECKING,
        account_label(checking[0]) if len(checking) == 1 else "Checking",
        "cash",
        sum((balance_of(a) for a in checking), Decimal("0")),
        today,
    )
    for a in accounts.values():
        if not a.in_plan:
            continue
        if a.type == "depository" and a.subtype != "checking" and balance_of(a) is not None:
            books[a.account_id] = _Book(a.account_id, account_label(a), "cash", balance_of(a), today)
        elif a.type in _DEBT_TYPES and a.current_balance is not None:
            # Plaid's current balance on a card or loan is what is owed.
            books[a.account_id] = _Book(
                a.account_id, account_label(a), "debt", a.current_balance, today
            )
    for bill in (
        await session.scalars(
            select(Bill).where(Bill.active.is_(True), Bill.balance.is_not(None))
        )
    ).all():
        books[f"bill:{bill.id}"] = _Book(f"bill:{bill.id}", bill.name, "debt", bill.balance, today)

    def cash_key(account_id: str | None) -> str:
        # Anything not tracked on its own is checking: unassigned payments,
        # checking itself, and a bank account with no balance to project.
        book = books.get(account_id or "")
        return account_id if book and book.account.kind == "cash" else CHECKING

    entries: list[tuple[tuple, LedgerEntry]] = []
    for p in upcoming.payments:
        entry = LedgerEntry(
            date=p.due_date, kind="payment", name=p.name, amount=p.amount, payment=p
        )
        if p.pay_from == "card":
            # Charged to a card: what is owed on it goes up, if it is tracked.
            entry.from_key = p.pay_from_account_id if p.pay_from_account_id in books else None
        else:
            entry.from_key = cash_key(p.pay_from_account_id)
        if p.source == "card" and p.ref_id in books:
            entry.to_key = p.ref_id
        elif p.source == "bill" and f"bill:{p.ref_id}" in books:
            entry.to_key = f"bill:{p.ref_id}"
        entries.append(((p.due_date, 0), entry))
    for kind, inflows in (("paycheck", paychecks), ("transfer", transfers)):
        for c in inflows:
            entry = LedgerEntry(
                date=c.date,
                kind=kind,
                name=c.name,
                amount=c.amount,
                inflow=c,
                to_key=cash_key(c.account_id),
            )
            entries.append(((c.date, 1), entry))
    entries.sort(key=lambda e: e[0])

    out = []
    for _, entry in entries:
        # Already paid, or an unknown amount: shown, but nothing moves.
        moves = entry.amount is not None and not (entry.payment and entry.payment.paid)
        for side in ("from", "to"):
            key = getattr(entry, f"{side}_key")
            if key is None:
                continue
            book = books[key]
            setattr(entry, f"{side}_name", book.account.name)
            setattr(entry, f"{side}_before", book.account.end_balance)
            if not moves:
                # Shown holding steady, so the balance reads through every row.
                setattr(entry, f"{side}_balance", book.account.end_balance)
                continue
            # Cash goes down when money leaves and up when it arrives; a debt
            # goes up when charged and down when paid.
            if book.account.kind == "cash":
                delta = -entry.amount if side == "from" else entry.amount
            else:
                delta = entry.amount if side == "from" else -entry.amount
            setattr(entry, f"{side}_balance", book.post(delta, entry.date))
        out.append(entry)

    return LedgerResponse(
        start=today,
        end=end,
        accounts=[b.account for b in books.values()],
        entries=out,
        paychecks=paychecks,
        transfers_in=transfers,
    )
