"""Build the monthly cash-flow Sankey: source -> Income -> group -> item.

Plaid amount sign convention: positive = money out (spending), negative =
money in (income). The effective category resolves override > rule/PFC, and
services.groups folds it into a handful of groups, each showing its biggest
items and rolling the rest into one.

Money moved between two of the user's own accounts is dropped: checking
down $500 and savings up $500 is neither income nor spending. Nor is paying
a linked card from checking — the purchases on the card are the spending,
and the card's "payment received" is not income. Either is recognised as
money out of one linked account matched by money in of the same amount to
another within a few days. With no such match it crossed the edge of what is
linked, so it counts — a Venmo cash-out is income, a Venmo payment to
someone is spending, a payment to a card that is not linked is debt paid.

An account left out of the plan (a spouse's own) is not the user's money:
its transactions are left out altogether, and money moved between it and
the user's accounts counts as coming in from it or going out to it.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Account, Transaction
from ..schemas import SankeyLink, SankeyNode, SankeyResponse
from .bills import account_label
from .groups import INCOME_SOURCES, OTHER, TRANSFERS, group_of, income_source_of

INCOME_NODE = "Income"
SAVINGS_NODE = "Savings / Unspent"
UNCATEGORIZED = "UNCATEGORIZED"

# Plaid PFC primaries for money moved between accounts. A card or loan
# payment is money moved too, when both ends are linked.
TRANSFER_PRIMARIES = {"TRANSFER_IN", "TRANSFER_OUT"}
_PAYMENT_PRIMARY = "LOAN_PAYMENTS"
_DEBT_TYPES = {"credit", "loan"}
# Items shown per group; the rest are rolled into the group's "Other".
_ITEMS_PER_GROUP = 5
# How far apart the two sides of an internal transfer may post: ACH between
# banks can take a couple of business days, plus a weekend.
_PAIR_WINDOW = timedelta(days=4)


def _month_bounds(month: str) -> tuple[date, date]:
    """'YYYY-MM' -> (first day, first day of next month)."""
    year, mon = (int(p) for p in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start, end


def _effective(txn: Transaction) -> tuple[str, str]:
    if txn.override is not None:
        primary = txn.override.category_primary
        detailed = txn.override.category_detailed or primary
    else:
        primary = txn.category_primary or txn.pfc_primary or UNCATEGORIZED
        detailed = txn.category_detailed or txn.pfc_detailed or primary
    return primary, detailed


def _internal_transfers(
    txns: list[Transaction], debt_accounts: set[str]
) -> dict[str, Transaction]:
    """Transactions that are one side of a move between linked accounts, by
    id, each with its other side.

    Money out is a transfer, or a card or loan payment. Money in is a
    transfer, or any credit to a card or loan — banks file the "payment
    received" line under all sorts, income included. Each out is paired
    with at most one in of the same amount on a different account, the
    closest pairs in date taken first.
    """
    outs = [
        t
        for t in txns
        if t.amount > 0
        and _effective(t)[0] in TRANSFER_PRIMARIES | {_PAYMENT_PRIMARY}
    ]
    ins = [
        t
        for t in txns
        if t.amount < 0
        and (_effective(t)[0] in TRANSFER_PRIMARIES or t.account_id in debt_accounts)
    ]
    # Closest pairs first, not earliest outs first: otherwise a $1,000 paid
    # to one card can claim the $1,000 another card received three days
    # before its own payment posted.
    pairs = sorted(
        (
            (abs(i.date - o.date), o.date, o, i)
            for o in outs
            for i in ins
            if i.account_id != o.account_id
            and -i.amount == o.amount
            and abs(i.date - o.date) <= _PAIR_WINDOW
        ),
        key=lambda p: (p[0], p[1]),
    )
    paired: dict[str, Transaction] = {}
    for _, _, out, into in pairs:
        if out.transaction_id in paired or into.transaction_id in paired:
            continue
        paired[out.transaction_id] = into
        paired[into.transaction_id] = out
    return paired


def own_flows(
    txns: list[Transaction], accounts: list[Account]
) -> tuple[list[Transaction], dict[str, str]]:
    """The transactions that are the user's money coming in or going out.

    Moves between the user's own accounts are dropped, and so is everything
    on an account left out of the plan. A move between the two is kept, with
    the other account's name by transaction id: money from a spouse's account
    is income from her, money sent to it is spending.
    """
    debt_accounts = {a.account_id for a in accounts if a.type in _DEBT_TYPES}
    outside = {a.account_id: a for a in accounts if not a.in_plan}
    pairs = _internal_transfers(txns, debt_accounts)
    kept: list[Transaction] = []
    via: dict[str, str] = {}
    for t in txns:
        if t.account_id in outside:
            continue
        other = pairs.get(t.transaction_id)
        if other is None:
            kept.append(t)
        elif other.account_id in outside:
            kept.append(t)
            via[t.transaction_id] = account_label(outside[other.account_id])
    return kept, via


async def build_sankey(session: AsyncSession, month: str) -> SankeyResponse:
    start, end = _month_bounds(month)
    # A few days either side, so a transfer that posts across the month
    # boundary still finds its other half. Twice the window, so that half's
    # own closer match is there to claim it: a card credited on Oct 2 and
    # paid from checking on Oct 5 is not September's payment of Sep 28.
    nearby = (
        await session.scalars(
            select(Transaction)
            .where(
                Transaction.date >= start - 2 * _PAIR_WINDOW,
                Transaction.date < end + 2 * _PAIR_WINDOW,
            )
            .options(selectinload(Transaction.override))
        )
    ).all()
    accounts = list((await session.scalars(select(Account))).all())
    kept, via = own_flows(list(nearby), accounts)
    txns = [t for t in kept if start <= t.date < end]

    total_income = 0.0
    total_spending = 0.0
    by_source: dict[str, float] = defaultdict(float)
    by_group: dict[str, float] = defaultdict(float)
    by_item: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for txn in txns:
        amount = float(txn.amount)
        if amount < 0:  # money in
            total_income += -amount
            source = (
                f"From {via[txn.transaction_id]}"
                if txn.transaction_id in via
                else income_source_of(*_effective(txn), txn.name)
            )
            by_source[source] += -amount
        elif amount > 0:  # money out
            total_spending += amount
            if txn.transaction_id in via:
                group, item = TRANSFERS, f"To {via[txn.transaction_id]}"
            else:
                group, item = group_of(*_effective(txn))
            by_group[group] += amount
            by_item[group][item] += amount

    links: list[SankeyLink] = []
    node_names: list[str] = []

    def add_node(name: str) -> None:
        if name not in node_names:
            node_names.append(name)

    # Where the money came from, in a fixed order so a source stays put
    # from month to month; money from an account outside the plan last.
    for source in INCOME_SOURCES + sorted(set(by_source) - set(INCOME_SOURCES)):
        if by_source.get(source):
            add_node(source)
            links.append(
                SankeyLink(source=source, target=INCOME_NODE, value=round(by_source[source], 2))
            )
    add_node(INCOME_NODE)

    for group, value in sorted(by_group.items(), key=lambda kv: kv[1], reverse=True):
        add_node(group)
        links.append(SankeyLink(source=INCOME_NODE, target=group, value=round(value, 2)))

    for group, _ in sorted(by_group.items(), key=lambda kv: kv[1], reverse=True):
        items = sorted(by_item[group].items(), key=lambda kv: kv[1], reverse=True)
        # Past the limit, the smaller items share one "Other" node, which
        # keeps the chart readable however many categories a month touches.
        if len(items) > _ITEMS_PER_GROUP:
            shown = items[: _ITEMS_PER_GROUP - 1]
            rest = sum(v for _, v in items[_ITEMS_PER_GROUP - 1 :])
            merged: dict[str, float] = defaultdict(float, shown)
            merged[OTHER[group] if group in OTHER else "Other"] += rest
            items = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
        for item, value in items:
            # A group of one item, named the same, needs no second column.
            if item == group:
                continue
            add_node(item)
            links.append(SankeyLink(source=group, target=item, value=round(value, 2)))

    leftover = round(total_income - total_spending, 2)
    if leftover > 0:
        add_node(SAVINGS_NODE)
        links.append(
            SankeyLink(source=INCOME_NODE, target=SAVINGS_NODE, value=leftover)
        )

    return SankeyResponse(
        month=month,
        nodes=[SankeyNode(name=n) for n in node_names],
        links=links,
        total_income=round(total_income, 2),
        total_spending=round(total_spending, 2),
    )
