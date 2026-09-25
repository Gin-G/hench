"""Upcoming payments and outstanding debt, merged across every source.

Three places know about money going out on a date:

* ``Bill`` — entered by hand, for what Plaid cannot see.
* ``Liability`` — card and loan due dates and minimums from /liabilities/get.
* ``RecurringStream`` — outflows Plaid detected in transaction history.

This module folds them into one timeline and one debt list, so the UI never
has to know where a payment came from to sort or total it.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Account, Bill, Item, RecurringStream
from ..schemas import DebtOut, DebtsResponse, UpcomingPayment, UpcomingResponse
from .schedule import PLAID_FREQUENCIES, next_on_or_after, occurrences

# Plaid account types that carry debt.
_DEBT_TYPES = {"credit", "loan"}

ZERO = Decimal("0")


def _account_label(account: Account) -> str:
    # A nickname is the user's own name for it, so it goes as-is, without the
    # mask that is only there to tell identically named accounts apart.
    if account.nickname:
        return account.nickname
    name = account.name or account.official_name or "Account"
    return f"{name} ••{account.mask}" if account.mask else name


def effective_due(bill: Bill, today: date) -> date:
    """The due date a bill should be shown with.

    A manual bill stays at its stored date, and so overdue, until it is marked
    paid. An autopay bill pays itself, so a date that has passed is assumed
    paid and the next occurrence is shown instead — without writing that back,
    since a GET should not move data.
    """
    if bill.autopay:
        return next_on_or_after(bill.next_due_date, bill.frequency, today, bill.due_day)
    return bill.next_due_date


def _bill_payments(bills: list[Bill], today: date, end: date) -> list[UpcomingPayment]:
    payments = []
    for bill in bills:
        for due in occurrences(
            effective_due(bill, today), bill.frequency, end, bill.due_day
        ):
            if bill.autopay and due < today:
                # A one-off autopay bill whose date has passed: paid, not due.
                continue
            payments.append(
                UpcomingPayment(
                    source="bill",
                    ref_id=str(bill.id),
                    name=bill.name,
                    due_date=due,
                    amount=bill.amount,
                    autopay=bill.autopay,
                    overdue=due < today,
                )
            )
    return payments


def _card_payments(
    accounts: list[Account], today: date, end: date
) -> list[UpcomingPayment]:
    payments = []
    for account in accounts:
        liability = account.liability
        if liability is None or liability.next_payment_due_date is None:
            continue
        due = liability.next_payment_due_date
        if due > end:
            continue
        # Plaid leaves the old due date in place until the next statement
        # closes. A past date that Plaid does not flag as overdue is therefore
        # almost always a cycle already paid, not a missed payment.
        if due < today and not liability.is_overdue:
            continue
        paid = bool(
            liability.last_payment_date
            and liability.last_statement_issue_date
            and liability.last_payment_date >= liability.last_statement_issue_date
        )
        payments.append(
            UpcomingPayment(
                source="card",
                ref_id=account.account_id,
                name=_account_label(account),
                due_date=due,
                amount=liability.minimum_payment_amount,
                amount_basis="minimum",
                statement_balance=liability.last_statement_balance,
                overdue=bool(liability.is_overdue),
                paid=paid,
                account=account.item.institution_name,
            )
        )
    return payments


def _stream_payments(
    streams: list[RecurringStream], labels: dict[str, str], today: date, end: date
) -> list[UpcomingPayment]:
    payments = []
    for stream in streams:
        if stream.predicted_next_date is None:
            continue
        frequency = PLAID_FREQUENCIES.get(stream.frequency or "")
        if stream.frequency == "SEMI_MONTHLY":
            frequency = "semi_monthly"
        # UNKNOWN frequency: trust the one predicted date and nothing beyond.
        for due in occurrences(stream.predicted_next_date, frequency or "once", end):
            # A prediction already in the past is stale, not overdue — the
            # stream is detected, not owed.
            if due < today:
                continue
            payments.append(
                UpcomingPayment(
                    source="recurring",
                    ref_id=stream.stream_id,
                    name=stream.merchant_name or stream.description or "Recurring",
                    due_date=due,
                    amount=stream.average_amount,
                    amount_basis="average",
                    account=labels.get(stream.account_id),
                )
            )
    return payments


async def build_upcoming(
    session: AsyncSession, today: date, days: int
) -> UpcomingResponse:
    end = today + timedelta(days=days)

    bills = list(
        (
            await session.scalars(
                select(Bill).where(Bill.active.is_(True), Bill.next_due_date <= end)
            )
        ).all()
    )
    accounts = list(
        (
            await session.scalars(
                select(Account).options(
                    selectinload(Account.liability), selectinload(Account.item)
                )
            )
        ).all()
    )
    # Loan payments and outbound transfers stay in. Filtering them by category
    # looked like it would avoid double-counting linked cards, but on real data
    # it dropped the mortgage, student loans and cards at unlinked lenders —
    # the payments Plaid can only see from the checking side. Where a stream
    # does duplicate a linked liability, the user hides it.
    streams = list(
        (
            await session.scalars(
                select(RecurringStream).where(
                    RecurringStream.direction == "outflow",
                    RecurringStream.is_active.is_(True),
                    RecurringStream.hidden.is_(False),
                )
            )
        ).all()
    )
    labels = {a.account_id: _account_label(a) for a in accounts}

    payments = (
        _bill_payments(bills, today, end)
        + _card_payments(accounts, today, end)
        + _stream_payments(streams, labels, today, end)
    )
    payments.sort(key=lambda p: (p.due_date, p.name.lower()))

    total_due = sum(
        (p.amount for p in payments if p.amount is not None and not p.paid), ZERO
    )
    return UpcomingResponse(start=today, end=end, payments=payments, total_due=total_due)


async def build_debts(session: AsyncSession, today: date) -> DebtsResponse:
    debts: list[DebtOut] = []

    rows = (
        await session.execute(
            select(Account, Item.institution_name)
            .join(Item, Account.item_id == Item.item_id)
            .options(selectinload(Account.liability))
            .where(Account.type.in_(_DEBT_TYPES))
        )
    ).all()
    for account, institution in rows:
        # For credit and loan accounts Plaid's current balance is what is owed.
        if account.current_balance is None:
            continue
        liability = account.liability
        debts.append(
            DebtOut(
                source="plaid",
                ref_id=account.account_id,
                name=_account_label(account),
                institution=institution,
                kind=liability.liability_type if liability else account.type,
                balance=account.current_balance,
                credit_limit=account.credit_limit,
                apr=(
                    (liability.purchase_apr or liability.interest_rate_percentage)
                    if liability
                    else None
                ),
                minimum_payment=liability.minimum_payment_amount if liability else None,
                next_due_date=liability.next_payment_due_date if liability else None,
                statement_balance=(
                    liability.last_statement_balance if liability else None
                ),
                is_overdue=bool(liability and liability.is_overdue),
            )
        )

    bills = (
        await session.scalars(
            select(Bill).where(Bill.active.is_(True), Bill.balance.is_not(None))
        )
    ).all()
    for bill in bills:
        debts.append(
            DebtOut(
                source="manual",
                ref_id=str(bill.id),
                name=bill.name,
                kind="loan",
                balance=bill.balance,
                apr=bill.apr,
                minimum_payment=bill.amount,
                next_due_date=effective_due(bill, today),
                is_overdue=not bill.autopay and bill.next_due_date < today,
            )
        )

    debts.sort(key=lambda d: d.balance, reverse=True)
    return DebtsResponse(
        debts=debts,
        total_balance=sum((d.balance for d in debts), ZERO),
        total_minimum=sum(
            (d.minimum_payment for d in debts if d.minimum_payment is not None), ZERO
        ),
    )
