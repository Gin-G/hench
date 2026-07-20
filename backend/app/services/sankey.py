"""Build the monthly cash-flow Sankey: Income -> primary -> detailed.

Plaid amount sign convention: positive = money out (spending), negative =
money in (income). Internal transfers are excluded so the flows aren't
double-counted. The effective category resolves override > rule/PFC.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Transaction
from ..schemas import SankeyLink, SankeyNode, SankeyResponse

INCOME_NODE = "Income"
SAVINGS_NODE = "Savings / Unspent"
UNCATEGORIZED = "UNCATEGORIZED"

# Plaid PFC primaries that represent internal movement, not income/spending.
TRANSFER_PRIMARIES = {"TRANSFER_IN", "TRANSFER_OUT"}


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


async def build_sankey(session: AsyncSession, month: str) -> SankeyResponse:
    start, end = _month_bounds(month)
    txns = (
        await session.scalars(
            select(Transaction)
            .where(Transaction.date >= start, Transaction.date < end)
            .options(selectinload(Transaction.override))
        )
    ).all()

    total_income = 0.0
    total_spending = 0.0
    # spending aggregated at the two link layers
    income_to_primary: dict[str, float] = defaultdict(float)
    primary_to_detailed: dict[tuple[str, str], float] = defaultdict(float)

    for txn in txns:
        amount = float(txn.amount)
        primary, detailed = _effective(txn)

        if primary in TRANSFER_PRIMARIES:
            continue

        if amount < 0:  # money in
            total_income += -amount
        elif amount > 0:  # money out
            total_spending += amount
            income_to_primary[primary] += amount
            primary_to_detailed[(primary, detailed)] += amount

    links: list[SankeyLink] = []
    node_names: list[str] = [INCOME_NODE]

    def add_node(name: str) -> None:
        if name not in node_names:
            node_names.append(name)

    for primary, value in sorted(
        income_to_primary.items(), key=lambda kv: kv[1], reverse=True
    ):
        add_node(primary)
        links.append(SankeyLink(source=INCOME_NODE, target=primary, value=round(value, 2)))

    for (primary, detailed), value in sorted(
        primary_to_detailed.items(), key=lambda kv: kv[1], reverse=True
    ):
        # Skip degenerate self-link when detailed == primary (single-tier cat).
        if detailed == primary:
            continue
        add_node(detailed)
        links.append(
            SankeyLink(source=primary, target=detailed, value=round(value, 2))
        )

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
