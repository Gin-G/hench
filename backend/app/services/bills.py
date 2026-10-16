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

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Account, Bill, Item, RecurringStream, Transaction
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


# Categories Plaid files a payment received on a card or loan under.
_PAYMENT_CATEGORIES = ("TRANSFER_IN", "LOAN_PAYMENTS", "LOAN_DISBURSEMENTS")

# How long before a statement date a card's payments are looked for, when a
# card reports no statement date at all.
_NO_STATEMENT_LOOKBACK = timedelta(days=35)


async def _card_credits(
    session: AsyncSession, accounts: dict[str, Account], today: date
) -> dict[str, list[tuple[date, Decimal]]]:
    """Payments posted to each card or loan, newest first, pending included.

    Seen from the card's own side, as credits. Plaid's category for the
    "Payment Received" line varies by bank — a transfer in or a loan payment,
    but Chase's come through as loan disbursements or even income — so the
    payment wording on the line counts too. Refunds are credits as well but
    carry the merchant's name and category, so they are not mistaken for
    payments.
    """
    debt_ids = [a.account_id for a in accounts.values() if a.type in ("credit", "loan")]
    if not debt_ids:
        return {}
    rows = (
        await session.execute(
            select(Transaction.account_id, Transaction.date, Transaction.amount)
            .where(
                Transaction.account_id.in_(debt_ids),
                Transaction.amount < 0,
                Transaction.date >= today - timedelta(days=62),
                or_(
                    Transaction.category_primary.in_(_PAYMENT_CATEGORIES),
                    Transaction.pfc_primary.in_(_PAYMENT_CATEGORIES),
                    Transaction.name.ilike("%payment%"),
                    Transaction.name.ilike("%autopay%"),
                ),
            )
            .order_by(Transaction.date.desc())
        )
    ).all()
    credits: dict[str, list[tuple[date, Decimal]]] = {}
    for account_id, on, amount in rows:
        credits.setdefault(account_id, []).append((on, -Decimal(amount)))
    return credits


def planned_payment(account: Account) -> Decimal | None:
    """What a card or loan is planned to be paid each month: the user's own
    figure where set, else a fixed autopay or the minimum, whichever is
    larger — the bank never pulls less than the minimum.
    """
    if account.payment_amount is not None:
        return account.payment_amount
    liability = account.liability
    minimum = liability.minimum_payment_amount if liability else None
    if account.autopay_amount is None:
        return minimum
    return max(account.autopay_amount, minimum or ZERO)


def _card_payments(
    accounts: dict[str, Account],
    today: date,
    end: date,
    credits: dict[str, list[tuple[date, Decimal]]] | None = None,
) -> list[UpcomingPayment]:
    payments = []
    for account in accounts.values():
        liability = account.liability
        if liability is None or liability.next_payment_due_date is None:
            continue
        if account.payment_hidden:
            # A detected payment from checking stands for it instead.
            continue
        due = liability.next_payment_due_date
        # A fixed monthly figure, the user's or the bank's autopay, is known
        # months ahead; a minimum is not.
        monthly = (
            account.payment_amount
            if account.payment_amount is not None
            else account.autopay_amount
        )
        # Never more than is owed.
        owed = max(account.current_balance or ZERO, ZERO)
        target = planned_payment(account)
        if target is not None and monthly is not None:
            target = min(target, owed)
        if account.payment_amount is not None:
            basis = "planned"
        elif account.autopay_amount is not None:
            basis = "autopay"
        else:
            basis = "minimum"
        common = dict(
            source="card",
            ref_id=account.account_id,
            name=account_label(account),
            amount_basis=basis,
            editable=True,
            autopay=True if account.autopay_amount is not None else None,
            account=account.item.institution_name,
            **pay_from(account.pay_from_account_id, accounts),
        )
        # Plaid leaves the old due date in place until the next statement
        # closes. A past date that Plaid does not flag as overdue is therefore
        # almost always a cycle already paid, not a missed payment.
        settled = due < today and not liability.is_overdue
        still_due = ZERO
        if not settled and due <= end:
            # Whether it is paid comes from the synced transactions alone:
            # payments posted to the card since the statement was issued.
            # Some banks (Barclays, for one) report the last payment's amount
            # but never its date, so the card's own transactions are the
            # dependable signal; the bank's date is used where given, unless
            # a fixed monthly figure says how much is coming.
            since = liability.last_statement_issue_date or (due - _NO_STATEMENT_LOOKBACK)
            cycle = [
                (on, amt)
                for on, amt in (credits or {}).get(account.account_id, [])
                if on > since
            ]
            paid_amount = sum((amt for _, amt in cycle), ZERO) or None
            paid = bool(paid_amount and paid_amount >= (target or ZERO))
            if monthly is None:
                paid = paid or bool(
                    liability.last_payment_date
                    and liability.last_statement_issue_date
                    and liability.last_payment_date >= liability.last_statement_issue_date
                )
            # Part of it already paid: only the rest is still due.
            amount = target
            if amount is not None and paid_amount and not paid:
                amount = amount - paid_amount
            still_due = ZERO if paid or amount is None else amount
            payments.append(
                UpcomingPayment(
                    **common,
                    due_date=due,
                    amount=amount,
                    statement_balance=liability.last_statement_balance,
                    overdue=bool(liability.is_overdue) and not paid,
                    paid=paid,
                    paid_amount=paid_amount,
                    paid_date=cycle[0][0] if cycle else None,
                )
            )
        if monthly is None:
            continue
        # The months after this one are projected at the same figure until
        # what is owed now is paid off. New charges would add to that, but
        # are not guessed at.
        remaining = owed - still_due
        for on in occurrences(due, "monthly", end, due.day):
            if on <= due or on < today:
                continue
            amount = min(monthly, remaining)
            if amount <= 0:
                break
            payments.append(
                UpcomingPayment(**common, due_date=on, amount=amount, estimated=True)
            )
            remaining -= amount
    return payments


# How far apart the two sides of one payment can post: checking a day or two
# after the card, or before it.
_SAME_PAYMENT_WITHIN = 4


async def _paid_cards(
    session: AsyncSession, streams: list[RecurringStream], accounts: dict[str, Account]
) -> dict[str, str]:
    """Detected payments that are a linked card's or loan's own payments.

    A stream seen leaving a bank account is matched, transaction by
    transaction, against the payments the linked cards received: the same
    amount to the cent within a few days. Chase pays all its cards from one
    autopay, so the card that most of a stream's payments landed on wins,
    and it takes two matches, or every one there is, to count.

    Returns stream_id -> the card's account_id.
    """
    cards = [
        a.account_id
        for a in accounts.values()
        if a.type in _DEBT_TYPES and a.in_plan and a.liability is not None
    ]
    candidates = [
        s
        for s in streams
        if s.transaction_ids
        and not s.not_duplicate
        and getattr(accounts.get(s.account_id), "type", None) == "depository"
    ]
    if not cards or not candidates:
        return {}
    ids = [tid for s in candidates for tid in s.transaction_ids]
    paid_out = {
        tid: (on, Decimal(amount))
        for tid, on, amount in (
            await session.execute(
                select(Transaction.transaction_id, Transaction.date, Transaction.amount)
                .where(Transaction.transaction_id.in_(ids), Transaction.amount > 0)
            )
        ).all()
    }
    if not paid_out:
        return {}
    earliest = min(on for on, _ in paid_out.values()) - timedelta(days=_SAME_PAYMENT_WITHIN)
    received = (
        await session.execute(
            select(Transaction.account_id, Transaction.date, Transaction.amount).where(
                Transaction.account_id.in_(cards),
                Transaction.amount < 0,
                Transaction.date >= earliest,
            )
        )
    ).all()
    matches: dict[str, str] = {}
    for stream in candidates:
        own = [paid_out[t] for t in stream.transaction_ids if t in paid_out]
        tally: dict[str, int] = {}
        for on, amount in own:
            for card, landed, credit in received:
                if (
                    -Decimal(credit) == amount
                    and abs((landed - on).days) <= _SAME_PAYMENT_WITHIN
                ):
                    tally[card] = tally.get(card, 0) + 1
                    break
        if not tally:
            continue
        card, n = max(tally.items(), key=lambda kv: kv[1])
        if n >= 2 or n == len(own):
            matches[stream.stream_id] = card
    return matches


def _is_loan_payment(stream: RecurringStream) -> bool:
    # Mortgages, student and personal loans, and payments to cards that are
    # not linked: all filed under LOAN_PAYMENTS.
    return stream.category_primary == "LOAN_PAYMENTS" or (
        stream.category_detailed or ""
    ).startswith("LOAN_PAYMENTS")


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
                    amount=(
                        stream.amount_override
                        if stream.amount_override is not None
                        else stream.average_amount
                    ),
                    amount_basis=(
                        "planned" if stream.amount_override is not None else "average"
                    ),
                    # A loan payment Plaid sees only from the paying side —
                    # a mortgage, a student loan — is planned like a card's.
                    editable=_is_loan_payment(stream),
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
    credits = await _card_credits(session, by_id, today)

    # Accounts left out of the plan: nothing they pay, and no card of theirs,
    # belongs on this timeline.
    excluded = {a.account_id for a in accounts if not a.in_plan}
    payments = (
        _bill_payments(bills, by_id, today, end)
        + _card_payments(by_id, today, end, credits)
        + _stream_payments(streams, by_id, today, end)
    )
    payments = [
        p
        for p in payments
        if p.pay_from_account_id not in excluded
        and not (p.source == "card" and p.ref_id in excluded)
    ]

    # A detected payment that is a linked card's own payment, seen from
    # checking, counts it twice. Flag both rows so the user keeps one; once
    # the card's own row is hidden, the stream is what pays the card down.
    paid_cards = await _paid_cards(session, streams, by_id)
    card_rows = {p.ref_id: p for p in payments if p.source == "card"}
    for p in payments:
        card_id = paid_cards.get(p.ref_id) if p.source == "recurring" else None
        if card_id is None or card_id in excluded:
            continue
        card = by_id[card_id]
        if card.payment_hidden:
            p.pays_account_id = card_id
            continue
        p.duplicate_source, p.duplicate_ref_id, p.duplicate_name = (
            "card", card_id, account_label(card)
        )
        row = card_rows.get(card_id)
        if row is not None and row.duplicate_ref_id is None:
            row.duplicate_source, row.duplicate_ref_id, row.duplicate_name = (
                "recurring", p.ref_id, p.name
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


def _payments_left(today: date, ends: date, next_due: date | None) -> int:
    """Monthly payments due from today up to the promo's end, at least one.

    Counted on the real due dates where known: a promo ending Sep 30 with
    payments due on the 1st has one fewer than the months between suggest.
    """
    if next_due is None:
        return max(1, math.ceil((ends - today).days / 30.4375))
    first = next_on_or_after(next_due, "monthly", today, next_due.day)
    return max(1, sum(1 for _ in occurrences(first, "monthly", ends, next_due.day)))


def _rates(
    debt: DebtOut,
    base_apr: Decimal | None,
    promo_apr: Decimal | None,
    promo_ends_on: date | None,
    promo_balance: Decimal | None,
    deferred: bool,
    today: date,
    planned: Decimal | None = None,
) -> DebtOut:
    """Fill in the rate fields, working out what a promo means today.

    ``planned`` is what will be paid each month: a fixed autopay, else the
    minimum.
    """
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
        left = _payments_left(today, promo_ends_on, debt.next_due_date)
        needed = (covered / left).quantize(CENT, rounding=ROUND_UP)
        update["promo_monthly_needed"] = needed
        update["promo_payments_left"] = left
        if planned is not None:
            update["promo_on_track"] = planned >= needed
            update["promo_left_at_end"] = max(covered - planned * left, ZERO)
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
            .where(Account.type.in_(_DEBT_TYPES), Account.in_plan.is_(True))
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
            autopay_amount=account.autopay_amount,
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
                planned_payment(account),
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
                bill.amount,
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
