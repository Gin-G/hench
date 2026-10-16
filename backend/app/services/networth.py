"""Net worth, how it has moved month to month, and the regular payments that
could go.

Linked accounts bring their own balances; the home, the car, retirement and
lenders Plaid cannot reach are holdings entered by hand. Past month-end
balances of linked accounts are worked back from today's through the
transactions since: a bank account held what it holds now, plus whatever
left it since, less whatever came in. A holding was worth its latest figure
on or before the date, or its first figure before it was entered at all.

Each month's spending is split three ways: debt payments, which end once the
debt is paid off; essentials, which do not (utilities, insurance, food, fuel,
and any payment the user marks so); and everything else. When spending beats
income, the bank accounts and the cards show where the rest came from: a
card balance that grew is new debt, one that shrank is debt paid down.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Account, Bill, Holding, RecurringStream, Transaction
from ..schemas import (
    CommitmentLine,
    Equity,
    HoldingOut,
    MonthFlow,
    NetWorthLine,
    NetWorthPoint,
    NetWorthResponse,
)
from .bills import _paid_cards, account_label, clean_name, planned_payment
from .groups import DEBT, FOOD, UTILITIES, group_of
from .ledger import balance_of
from .sankey import _effective, own_flows

ZERO = Decimal("0")
CENT = Decimal("0.01")
_DEBT_TYPES = {"credit", "loan"}

# Spending groups that stay once the debts are gone, and items outside them.
_ESSENTIAL_GROUPS = {UTILITIES, FOOD}
_ESSENTIAL_ITEMS = {"Fuel"}

# Monthly equivalents of Plaid's and the bills' frequencies.
_PER_MONTH = {
    "WEEKLY": Decimal(52) / 12,
    "BIWEEKLY": Decimal(26) / 12,
    "SEMI_MONTHLY": Decimal(2),
    "MONTHLY": Decimal(1),
    "ANNUALLY": Decimal(1) / 12,
    "weekly": Decimal(52) / 12,
    "biweekly": Decimal(26) / 12,
    "monthly": Decimal(1),
    "quarterly": Decimal(1) / 3,
    "annually": Decimal(1) / 12,
}


def _month_end(year: int, month: int) -> date:
    first_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first_next - timedelta(days=1)


def _months(first: date, today: date) -> list[tuple[str, date, date]]:
    """('YYYY-MM', first day, last day or today) from first's month to today's."""
    out = []
    year, month = first.year, first.month
    while (year, month) <= (today.year, today.month):
        end = min(_month_end(year, month), today)
        out.append((f"{year:04d}-{month:02d}", date(year, month, 1), end))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _guess_commitment(primary: str | None, detailed: str | None) -> str:
    """Debt, essential or optional, from a payment's category."""
    group, item = group_of(primary or "", detailed or primary or "")
    if group == DEBT:
        return "debt"
    if group in _ESSENTIAL_GROUPS or item in _ESSENTIAL_ITEMS:
        return "essential"
    return "optional"


def _holding_value(holding: Holding, on: date) -> Decimal:
    """The latest figure on or before ``on``; before the first, the first."""
    value = None
    for v in holding.values:
        if v.as_of <= on:
            value = v.value
        else:
            break
    if value is None:
        value = holding.values[0].value if holding.values else holding.value
    return value


def _q(x: Decimal | float) -> Decimal:
    return Decimal(x).quantize(CENT)


async def build_networth(session: AsyncSession, today: date) -> NetWorthResponse:
    accounts = list(
        (
            await session.scalars(
                select(Account).options(
                    selectinload(Account.liability), selectinload(Account.item)
                )
            )
        ).all()
    )
    by_id = {a.account_id: a for a in accounts}
    # An account left out of the plan (a spouse's own, say) is not the
    # user's to count.
    mine = [a for a in accounts if a.in_plan]
    holdings = list(
        (
            await session.scalars(
                select(Holding).options(selectinload(Holding.values)).order_by(Holding.id)
            )
        ).all()
    )
    txns = list(
        (
            await session.scalars(
                select(Transaction).options(selectinload(Transaction.override))
            )
        ).all()
    )

    # --- Today ---------------------------------------------------------------
    lines: list[NetWorthLine] = []
    for a in mine:
        debt = a.type in _DEBT_TYPES
        value = a.current_balance if debt else balance_of(a)
        if value is None:
            continue
        lines.append(
            NetWorthLine(
                source="linked",
                ref_id=a.account_id,
                name=account_label(a),
                kind="debt" if debt else "asset",
                category=a.subtype or a.type or "other",
                value=value,
                as_of=a.balances_updated_at.date() if a.balances_updated_at else None,
                detail=a.item.institution_name if a.item else None,
            )
        )
    for h in holdings:
        lines.append(
            NetWorthLine(
                source="manual",
                ref_id=str(h.id),
                name=h.name,
                kind=h.kind,
                category=h.category,
                value=h.value,
                as_of=h.as_of,
                holding=HoldingOut.model_validate(h),
            )
        )
    assets = sum((l.value for l in lines if l.kind == "asset"), ZERO)
    debts = sum((l.value for l in lines if l.kind == "debt"), ZERO)

    equity = []
    for asset in (h for h in holdings if h.kind == "asset"):
        secured = [h for h in holdings if h.kind == "debt" and h.secured_by_id == asset.id]
        if secured:
            owed = sum((h.value for h in secured), ZERO)
            equity.append(
                Equity(name=asset.name, value=asset.value, owed=owed, equity=asset.value - owed)
            )

    # --- History -------------------------------------------------------------
    first = min((t.date for t in txns), default=today)
    months = _months(first, today)
    mine_ids = {a.account_id for a in mine}
    # Each linked account's balance at a month's end: today's, with every
    # transaction dated after it undone. A card's current balance is posted
    # items only, so its pending ones are not undone.
    later: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.account_id in mine_ids:
            later[t.account_id].append(t)

    def linked_at(on: date) -> tuple[Decimal, Decimal]:
        held = owed = ZERO
        for a in mine:
            debt = a.type in _DEBT_TYPES
            now = a.current_balance if debt else balance_of(a)
            if now is None:
                continue
            after = sum(
                (
                    Decimal(t.amount)
                    for t in later[a.account_id]
                    if t.date > on and not (debt and t.pending)
                ),
                ZERO,
            )
            if debt:
                owed += now - after
            else:
                held += now + after
        return held, owed

    history = []
    for label, _, end in months:
        held, owed = linked_at(end)
        manual_assets = sum(
            (_holding_value(h, end) for h in holdings if h.kind == "asset"), ZERO
        )
        manual_debts = sum(
            (_holding_value(h, end) for h in holdings if h.kind == "debt"), ZERO
        )
        history.append(
            NetWorthPoint(
                month=label,
                on=end,
                assets=_q(held + manual_assets),
                debts=_q(owed + manual_debts),
                net_worth=_q(held + manual_assets - owed - manual_debts),
                liquid=_q(held - owed),
            )
        )

    # --- Month by month ------------------------------------------------------
    # The same income and spending as the cash-flow chart: the user's own
    # accounts, moves between them left out.
    streams = list(
        (
            await session.scalars(
                select(RecurringStream).where(RecurringStream.direction == "outflow")
            )
        ).all()
    )
    chosen = {
        tid: s.commitment
        for s in streams
        if s.commitment and s.transaction_ids
        for tid in s.transaction_ids
    }
    debt_accounts = {a.account_id for a in accounts if a.type in _DEBT_TYPES}
    kept, via = own_flows(txns, accounts)
    counted = {t.transaction_id for t in kept}
    flows = []
    for label, start, end in months:
        sums: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for t in txns:
            if not start <= t.date <= end or t.account_id not in mine_ids:
                continue
            amount = Decimal(t.amount)
            if t.account_id in debt_accounts:
                if amount > 0:
                    sums["card_charged"] += amount
                else:
                    sums["card_paid"] -= amount
            else:
                sums["cash_change"] -= amount
            if t.transaction_id not in counted:
                continue
            if amount < 0:
                sums["income"] -= amount
            elif amount > 0:
                kind = (
                    "optional"
                    if t.transaction_id in via
                    else chosen.get(t.transaction_id) or _guess_commitment(*_effective(t))
                )
                sums["optional" if kind == "optional" else kind] += amount
        spending = sums["essential"] + sums["debt"] + sums["optional"]
        flows.append(
            MonthFlow(
                month=label,
                partial=end == today and end != _month_end(end.year, end.month),
                income=_q(sums["income"]),
                essential=_q(sums["essential"]),
                debt=_q(sums["debt"]),
                other=_q(sums["optional"]),
                spending=_q(spending),
                net=_q(sums["income"] - spending),
                cash_change=_q(sums["cash_change"]),
                card_charged=_q(sums["card_charged"]),
                card_paid=_q(sums["card_paid"]),
                card_change=_q(sums["card_charged"] - sums["card_paid"]),
            )
        )

    # --- Regular payments ----------------------------------------------------
    commitments: list[CommitmentLine] = []
    visible = [
        s
        for s in streams
        if s.is_active and not s.hidden and s.account_id in mine_ids
    ]
    # A detected payment that is a linked card's own payment is the card's
    # row below, unless the card's row is the one hidden.
    paid_cards = await _paid_cards(session, visible, by_id)
    for a in mine:
        if a.type not in _DEBT_TYPES or a.payment_hidden:
            continue
        owed = a.current_balance or ZERO
        planned = planned_payment(a)
        if owed <= 0 or not planned:
            continue
        commitments.append(
            CommitmentLine(
                source="card",
                ref_id=a.account_id,
                name=account_label(a),
                commitment="debt",
                chosen=False,
                monthly=min(planned, owed),
                frequency="MONTHLY",
                owed=owed,
            )
        )
    for s in visible:
        card = paid_cards.get(s.stream_id)
        if card is not None and not by_id[card].payment_hidden:
            continue
        # Interest is charged to the card, and already in its payment.
        if s.category_detailed == "BANK_FEES_INTEREST_CHARGE":
            continue
        amount = s.amount_override if s.amount_override is not None else s.average_amount
        if not amount:
            continue
        commitments.append(
            CommitmentLine(
                source="recurring",
                ref_id=s.stream_id,
                name=s.merchant_name or clean_name(s.description or "Recurring payment"),
                commitment=s.commitment
                or _guess_commitment(s.category_primary, s.category_detailed),
                chosen=s.commitment is not None,
                monthly=_q(abs(amount) * _PER_MONTH.get(s.frequency or "", Decimal(1))),
                frequency=s.frequency,
            )
        )
    bills = (await session.scalars(select(Bill).where(Bill.active.is_(True)))).all()
    for b in bills:
        if b.amount is None or b.frequency == "once":
            continue
        commitments.append(
            CommitmentLine(
                source="bill",
                ref_id=str(b.id),
                name=b.name,
                commitment="debt"
                if b.balance
                else _guess_commitment(b.category, b.category),
                chosen=False,
                monthly=_q(b.amount * _PER_MONTH.get(b.frequency, Decimal(1))),
                frequency=b.frequency.upper(),
                owed=b.balance,
            )
        )
    commitments.sort(key=lambda c: (c.commitment, -c.monthly))

    return NetWorthResponse(
        as_of=today,
        assets=assets,
        debts=debts,
        net_worth=assets - debts,
        lines=lines,
        equity=equity,
        history=history,
        months=flows,
        commitments=commitments,
    )
