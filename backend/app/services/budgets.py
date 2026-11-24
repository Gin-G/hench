"""Monthly budgets, and an alert for every charge against one.

A budget limits one spending group or one item in it, as the cash-flow chart
names them, for the calendar month. What counts is what the chart counts:
spending from the user's own accounts, moves between them left out. Refunds
are not netted off, as they are not on the chart; pending charges count, so
a budget is current the moment a card is swiped.

After each sync, every charge not looked at before that falls in this month
and under a budget is pushed to the user's ntfy topic, one notification per
budget, with what is left of it. Everything new is then remembered as seen,
budgeted or not, so a budget set up mid-month starts quiet; a charge whose
push failed is not, so the next sync tries again.
"""
from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..models import Account, AlertedTransaction, Budget, Transaction, User
from ..schemas import (
    AlertSettings,
    BudgetCharge,
    BudgetOut,
    BudgetsResponse,
    CategoryGroup,
    CategoryItem,
)
from . import notify
from .bills import clean_name
from .groups import _DETAILED, _PRIMARY, GROUPS, group_of
from .sankey import _effective, own_flows

log = logging.getLogger("hench.budgets")

ZERO = Decimal("0")
CENT = Decimal("0.01")
# Own-account transfers are paired across a few days; load that much before
# the month so one straddling its start is still recognised.
_PAIR_SLACK = timedelta(days=10)
# Lines of charges in one notification before the rest are summarised.
_MAX_LINES = 4


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _months_back(d: date, n: int) -> date:
    """The first of the month n months before d's."""
    y, m = d.year, d.month - n
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def _label(t: Transaction) -> str:
    return t.merchant_name or clean_name(t.name or "") or "Unknown"


def budget_name(b: Budget) -> str:
    return b.item or b.group


def _matches(b: Budget, key: tuple[str, str]) -> bool:
    return key[0] == b.group and (b.item is None or key[1] == b.item)


async def _spending(
    session: AsyncSession, start: date, today: date
) -> list[tuple[Transaction, tuple[str, str]]]:
    """Spending from start to today, each with its (group, item)."""
    accounts = (await session.scalars(select(Account))).all()
    txns = (
        await session.scalars(
            select(Transaction)
            .where(Transaction.date >= start - _PAIR_SLACK, Transaction.date <= today)
            .options(selectinload(Transaction.override))
        )
    ).all()
    kept, _ = own_flows(list(txns), list(accounts))
    out = []
    for t in kept:
        if t.date < start or Decimal(t.amount) <= 0:
            continue
        out.append((t, group_of(*_effective(t))))
    return out


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT)


def alert_settings(user: User) -> AlertSettings:
    if not notify.available():
        return AlertSettings(available=False, enabled=False)
    server = notify.public_server()
    topic = notify.topic_for(user)
    return AlertSettings(
        available=True,
        enabled=user.alerts_enabled,
        server=server,
        topic=topic,
        subscribe_url=f"{server}/{topic}",
    )


async def build_budgets(session: AsyncSession, user: User, today: date) -> BudgetsResponse:
    start = _month_start(today)
    history_start = _months_back(today, 3)
    spending = await _spending(session, history_start, today)
    budgets = (await session.scalars(select(Budget).order_by(Budget.group, Budget.item.nulls_first()))).all()

    # Averages over the last three full months, for choosing an amount.
    per_item: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    per_group: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t, key in spending:
        if t.date < start:
            per_item[key] += Decimal(t.amount)
            per_group[key[0]] += Decimal(t.amount)
    items_by_group: dict[str, set[str]] = defaultdict(set)
    for g, i in list(_DETAILED.values()) + list(_PRIMARY.values()):
        items_by_group[g].add(i)
    for g, i in per_item:
        items_by_group[g].add(i)
    order = GROUPS + sorted(set(items_by_group) - set(GROUPS))
    categories = [
        CategoryGroup(
            group=g,
            average=_q(per_group[g] / 3),
            items=sorted(
                (CategoryItem(item=i, average=_q(per_item[(g, i)] / 3)) for i in items_by_group[g]),
                key=lambda c: (-c.average, c.item),
            ),
        )
        for g in order
        if g in items_by_group
    ]

    days = calendar.monthrange(today.year, today.month)[1]
    this_month = [(t, k) for t, k in spending if t.date >= start]
    out = []
    for b in budgets:
        charges = [t for t, k in this_month if _matches(b, k)]
        spent = sum((Decimal(t.amount) for t in charges), ZERO)
        charges.sort(key=lambda t: -Decimal(t.amount))
        out.append(
            BudgetOut(
                id=b.id,
                group=b.group,
                item=b.item,
                name=budget_name(b),
                amount=b.amount,
                spent=_q(spent),
                remaining=_q(b.amount - spent),
                pace=_q(b.amount * today.day / days),
                charges=[
                    BudgetCharge(
                        transaction_id=t.transaction_id,
                        date=t.date,
                        name=_label(t),
                        amount=Decimal(t.amount),
                        pending=t.pending,
                    )
                    for t in charges[:8]
                ],
            )
        )
    # A group's budget already covers its items' budgets, so the total counts
    # an item only when its group has none.
    whole = {b.group for b in budgets if b.item is None}
    counted = [o for o in out if o.item is None or o.group not in whole]
    return BudgetsResponse(
        month=f"{today:%Y-%m}",
        days_in_month=days,
        day=today.day,
        budgets=out,
        total_amount=sum((o.amount for o in counted), ZERO),
        total_spent=sum((o.spent for o in counted), ZERO),
        categories=categories,
        alerts=alert_settings(user),
    )


def _money(x: Decimal) -> str:
    return f"${x:,.2f}"


def _message(
    b: Budget, new: list[Transaction], spent: Decimal, today: date
) -> tuple[str, str, int, list[str]]:
    """Title, body, priority and tags for the charges just made against b."""
    left = b.amount - spent
    name = budget_name(b)
    lines = [
        f"{_label(t)} {_money(Decimal(t.amount))}" + (" (pending)" if t.pending else "")
        for t in new[:_MAX_LINES]
    ]
    if len(new) > _MAX_LINES:
        rest = sum((Decimal(t.amount) for t in new[_MAX_LINES:]), ZERO)
        lines.append(f"+{len(new) - _MAX_LINES} more, {_money(rest)}")
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day
    month = today.strftime("%B")
    lines.append(
        f"{_money(spent)} of {_money(b.amount)} spent in {month}"
        f" · {days_left} day{'s' if days_left != 1 else ''} left"
    )
    pct = spent / b.amount if b.amount else Decimal(1)
    if left < 0:
        return f"{name}: over by {_money(-left)}", "\n".join(lines), 4, ["rotating_light"]
    if pct >= Decimal("0.8"):
        return f"{name}: {_money(left)} left", "\n".join(lines), 4, ["warning"]
    return f"{name}: {_money(left)} left", "\n".join(lines), 3, ["moneybag"]


async def alert_new_charges(session: AsyncSession, user: User, today: date) -> int:
    """Push the charges since the last sync against each budget; returns how
    many notifications went out. Runs in the user's scope; the caller commits."""
    candidates = (
        await session.execute(
            select(Transaction.transaction_id, Transaction.pending_transaction_id).where(
                Transaction.transaction_id.not_in(select(AlertedTransaction.transaction_id))
            )
        )
    ).all()
    if not candidates:
        return 0
    new_ids = {tid for tid, _ in candidates}
    # A posted charge whose pending one was seen is not new.
    replaced = {p for _, p in candidates if p}
    seen_pending = set(
        (
            await session.scalars(
                select(AlertedTransaction.transaction_id).where(
                    AlertedTransaction.transaction_id.in_(replaced)
                )
            )
        ).all()
    ) if replaced else set()

    failed: set[str] = set()
    sent = 0
    budgets = (await session.scalars(select(Budget))).all()
    if budgets and user.alerts_enabled and notify.available():
        this_month = await _spending(session, _month_start(today), today)
        click = (get_settings().app_url.rstrip("/") + "/?view=budgets") if get_settings().app_url else None
        for b in budgets:
            mine = [(t, k) for t, k in this_month if _matches(b, k)]
            new = [
                t
                for t, _ in mine
                if t.transaction_id in new_ids and t.pending_transaction_id not in seen_pending
            ]
            if not new:
                continue
            new.sort(key=lambda t: (t.date, t.transaction_id))
            spent = sum((Decimal(t.amount) for t, _ in mine), ZERO)
            title, body, priority, tags = _message(b, new, spent, today)
            try:
                await notify.send(user, title, body, priority=priority, tags=tags, click=click)
                sent += 1
            except notify.NotifyError:
                log.exception("budget alert for user=%s budget=%s failed", user.id, b.id)
                failed.update(t.transaction_id for t in new)

    done = new_ids - failed
    if done:
        await session.execute(
            pg_insert(AlertedTransaction)
            .values([{"user_id": user.id, "transaction_id": tid} for tid in done])
            .on_conflict_do_nothing()
        )
    log.info("budget alerts user=%s: %d new charges, %d sent", user.id, len(new_ids), sent)
    return sent
