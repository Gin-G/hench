"""Upcoming payments and outstanding debt, merged across every source.

Three places know about money going out on a date:

* ``Bill`` — entered by hand, for what Plaid cannot see.
* ``Liability`` — card and loan due dates and minimums from /liabilities/get.
* ``RecurringStream`` — outflows Plaid detected in transaction history.

This module folds them into one timeline and one debt list, so the UI never
has to know where a payment came from to sort or total it.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from decimal import ROUND_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Account, Bill, Item, RecurringStream
from ..schemas import DebtOut, DebtsResponse, UpcomingPayment, UpcomingResponse
from .schedule import PLAID_FREQUENCIES, next_on_or_after, occurrences

# Plaid account types that carry debt.
_DEBT_TYPES = {"credit", "loan"}

ZERO = Decimal("0")
CENT = Decimal("0.01")


# ACH descriptions carry the originator's id after the name, e.g.
# "WF Credit Card AUTO PAY PPD ID: 50260000".
_ACH_SUFFIX = re.compile(r"\s+(?:PPD|CCD|WEB|TEL)\s+ID:.*$", re.I)


def clean_name(name: str) -> str:
    return _ACH_SUFFIX.sub("", name).strip() or name


def account_label(account: Account) -> str:
    # A nickname is the user's own name for it, so it goes as-is, without the
    # mask that is only there to tell identically named accounts apart.
    if account.nickname:
        return account.nickname
    name = account.name or account.official_name or "Account"
    return f"{name} ••{account.mask}" if account.mask else name


def pay_from(
    account_id: str | None, accounts: dict[str, Account]
) -> dict[str, str | None]:
    """Where a payment's money leaves, as UpcomingPayment fields.

    A credit card makes it a card charge, paid through that card's own
    payment. Anything else, or no account at all, is cash on the due date.
    """
    account = accounts.get(account_id) if account_id else None
    if account is None:
        return {"pay_from": "cash", "pay_from_account_id": None, "pay_from_name": None}
    return {
        "pay_from": "card" if account.type == "credit" else "cash",
        "pay_from_account_id": account.account_id,
        "pay_from_name": account_label(account),
    }


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


def _bill_payments(
    bills: list[Bill], accounts: dict[str, Account], today: date, end: date
) -> list[UpcomingPayment]:
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
                    # Only the next occurrence of a synced bill is the biller's
                    # own figure; later ones are projections either way.
                    estimated=bill.due_date_estimated
                    or (bill.source is not None and due != bill.next_due_date),
                    synced_from=bill.source,
                    **pay_from(bill.pay_from_account_id, accounts),
                )
            )
    return payments


def _card_payments(
    accounts: dict[str, Account], today: date, end: date
) -> list[UpcomingPayment]:
    payments = []
    for account in accounts.values():
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
                name=account_label(account),
                due_date=due,
                amount=liability.minimum_payment_amount,
                amount_basis="minimum",
                statement_balance=liability.last_statement_balance,
                overdue=bool(liability.is_overdue),
                paid=paid,
                account=account.item.institution_name,
                **pay_from(account.pay_from_account_id, accounts),
            )
        )
    return payments


def _stream_payments(
    streams: list[RecurringStream],
    accounts: dict[str, Account],
    today: date,
    end: date,
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
                    name=clean_name(stream.merchant_name or stream.description or "Recurring"),
                    due_date=due,
                    amount=stream.average_amount,
                    amount_basis="average",
                    account=(
                        account_label(accounts[stream.account_id])
                        if stream.account_id in accounts
                        else None
                    ),
                    estimated=True,
                    # Plaid saw it leave this account, so that is what pays it.
                    **pay_from(stream.account_id, accounts),
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
    by_id = {a.account_id: a for a in accounts}

    payments = (
        _bill_payments(bills, by_id, today, end)
        + _card_payments(by_id, today, end)
        + _stream_payments(streams, by_id, today, end)
    )
    payments.sort(key=lambda p: (p.due_date, p.name.lower()))

    # Card-charged payments are already inside that card's own payment.
    total_due = sum(
        (
            p.amount
            for p in payments
            if p.amount is not None and not p.paid and p.pay_from == "cash"
        ),
        ZERO,
    )
    return UpcomingResponse(start=today, end=end, payments=payments, total_due=total_due)


def _months_left(today: date, ends: date) -> int:
    # Payments left before the promo ends, one per month, at least one.
    return max(1, math.ceil((ends - today).days / 30.4375))


def _rates(
    debt: DebtOut,
    base_apr: Decimal | None,
    promo_apr: Decimal | None,
    promo_ends_on: date | None,
    promo_balance: Decimal | None,
    deferred: bool,
    today: date,
) -> DebtOut:
    """Fill in the rate fields, working out what a promo means today."""
    active = (
        promo_apr is not None and promo_ends_on is not None and promo_ends_on >= today
    )
    update: dict = {
        "base_apr": base_apr,
        "apr": base_apr,
        "promo_apr": promo_apr,
        "promo_ends_on": promo_ends_on,
        "promo_balance": promo_balance,
        "promo_deferred_interest": deferred,
        "promo_active": active,
    }
    if active and debt.balance > 0:
        covered = min(promo_balance, debt.balance) if promo_balance is not None else debt.balance
        # Payments above the minimum go to the highest-rate part of a balance
        # first, so while any of it is outside the promo, that part is what
        # an extra dollar pays down.
        if covered >= debt.balance:
            update["apr"] = promo_apr
        needed = (covered / _months_left(today, promo_ends_on)).quantize(
            CENT, rounding=ROUND_UP
        )
        update["promo_monthly_needed"] = needed
        if debt.minimum_payment is not None:
            update["promo_on_track"] = debt.minimum_payment >= needed
    return debt.model_copy(update=update)


def _target(debts: list[DebtOut]) -> tuple[DebtOut | None, str | None]:
    """Where extra money should go now.

    A deferred-interest promo the minimum will not clear in time comes first,
    soonest end date first: missing it charges back every month of waived
    interest at once. Otherwise the highest current rate, the smaller balance
    on a tie since it clears sooner. A 0% promo sits low until it ends, then
    ranks by its regular rate from that day.
    """
    owing = [d for d in debts if d.balance > 0]
    at_risk = [
        d
        for d in owing
        if d.promo_active and d.promo_deferred_interest and d.promo_on_track is not True
    ]
    if at_risk:
        d = min(at_risk, key=lambda d: d.promo_ends_on)
        return d, (
            f"Deferred-interest promo ends {d.promo_ends_on:%b %-d, %Y}: "
            f"${d.promo_monthly_needed:,.2f}/mo clears it in time"
            + ("" if d.minimum_payment is None else f", the ${d.minimum_payment:,.2f} minimum does not")
            + "."
        )
    rated = [d for d in owing if d.apr is not None and d.apr > 0]
    if not rated:
        return None, None
    d = max(rated, key=lambda d: (d.apr, -d.balance))
    reason = f"Highest rate right now, {d.apr:.2f}% APR."
    if d.promo_active and d.apr != d.promo_apr:
        reason += (
            f" Part of it is on a {d.promo_apr:.2f}% promo; extra payments go to"
            " the rest first."
        )
    return d, reason


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
        plaid_apr = (
            (liability.purchase_apr or liability.interest_rate_percentage)
            if liability
            else None
        )
        debt = DebtOut(
            source="plaid",
            ref_id=account.account_id,
            name=account_label(account),
            institution=institution,
            kind=liability.liability_type if liability else account.type,
            balance=account.current_balance,
            credit_limit=account.credit_limit,
            apr_overridden=account.apr_override is not None,
            plaid_aprs=(liability.aprs or []) if liability else [],
            minimum_payment=liability.minimum_payment_amount if liability else None,
            next_due_date=liability.next_payment_due_date if liability else None,
            statement_balance=(
                liability.last_statement_balance if liability else None
            ),
            is_overdue=bool(liability and liability.is_overdue),
        )
        debts.append(
            _rates(
                debt,
                account.apr_override if account.apr_override is not None else plaid_apr,
                account.promo_apr,
                account.promo_ends_on,
                account.promo_balance,
                account.promo_deferred_interest,
                today,
            )
        )

    bills = (
        await session.scalars(
            select(Bill).where(Bill.active.is_(True), Bill.balance.is_not(None))
        )
    ).all()
    for bill in bills:
        debt = DebtOut(
            source="manual",
            ref_id=str(bill.id),
            name=bill.name,
            kind="loan",
            balance=bill.balance,
            minimum_payment=bill.amount,
            next_due_date=effective_due(bill, today),
            is_overdue=not bill.autopay and bill.next_due_date < today,
        )
        debts.append(
            _rates(
                debt,
                bill.apr,
                bill.promo_apr,
                bill.promo_ends_on,
                bill.promo_balance,
                bill.promo_deferred_interest,
                today,
            )
        )

    # Highest current rate first; unknown rates last, biggest balance first
    # within a rate.
    debts.sort(key=lambda d: (d.apr is None, -(d.apr or 0), -d.balance))
    target, reason = _target(debts)
    return DebtsResponse(
        debts=debts,
        total_balance=sum((d.balance for d in debts), ZERO),
        total_minimum=sum(
            (d.minimum_payment for d in debts if d.minimum_payment is not None), ZERO
        ),
        target=target,
        target_reason=reason,
    )
