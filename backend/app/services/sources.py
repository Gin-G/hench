"""Helpers shared by the fetchers that keep a Bill up to date from outside.

Each source (a portal, a mailbox) ends up in the same place — a ``Bill`` with
``source`` set — and needs the same few things: reading money and dates out of
text, noticing that Plaid has already seen the payment, and hiding Plaid's own
detected stream for the biller so the bill is not counted twice.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from html import unescape

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RecurringStream, Transaction

_MONEY = re.compile(r"(-?)\$\s*(-?[\d,]+\.\d{2})")


def parse_money(text: str) -> Decimal | None:
    """The first dollar amount in ``text``; negative if written with a minus."""
    m = _MONEY.search(text)
    if not m:
        return None
    value = Decimal(m.group(2).replace(",", ""))
    return -abs(value) if m.group(1) or value < 0 else value


def parse_date(text: str) -> date | None:
    """The first date in ``text``: 2026-09-28, 09/28/2026, or Sep 28, 2026."""
    for pattern, formats in (
        (r"\d{4}-\d{2}-\d{2}", ("%Y-%m-%d",)),
        (r"\d{1,2}/\d{1,2}/\d{4}", ("%m/%d/%Y",)),
        (r"\d{1,2}/\d{1,2}/\d{2}\b", ("%m/%d/%y",)),
        (r"[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}", ("%B %d, %Y", "%b %d, %Y")),
    ):
        m = re.search(pattern, text)
        if not m:
            continue
        found = m.group(0).replace(".", "")
        for fmt in formats:
            try:
                return datetime.strptime(found, fmt).date()
            except ValueError:
                pass
    return None


def visible_lines(html: str) -> list[str]:
    """The text a reader would see, one non-empty line per block."""
    html = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    lines = []
    for line in re.sub(r"<[^>]+>", "\n", html).split("\n"):
        line = re.sub(r"\s+", " ", unescape(line)).strip()
        if line:
            lines.append(line)
    return lines


def value_after(lines: list[str], label: re.Pattern, parse, window: int = 3):
    """Parse the first value on, or within ``window`` lines after, a label.

    Bill layouts put a value beside its label or in the next cell, which after
    stripping tags lands on the same line or the one after.
    """
    for i, line in enumerate(lines):
        if label.search(line):
            for candidate in lines[i : i + window]:
                found = parse(candidate)
                if found is not None:
                    return found
    return None


async def paid_per_bank(
    session: AsyncSession, pattern: str, since: date, amount: Decimal
) -> date | None:
    """Date of a bank or card payment matching ``pattern`` and ``amount``.

    Billers can take days to post a payment; Plaid often sees it first.
    """
    rows = (
        await session.execute(
            select(Transaction.date, Transaction.amount).where(
                or_(
                    Transaction.name.ilike(pattern),
                    Transaction.merchant_name.ilike(pattern),
                ),
                Transaction.date >= since,
            )
        )
    ).all()
    for on, paid in rows:
        if abs(Decimal(paid) - amount) <= Decimal("1.00"):
            return on
    return None


async def hide_detected_streams(session: AsyncSession, pattern: str) -> None:
    """Hide Plaid's own guess at a bill that a source now supplies."""
    streams = (
        await session.scalars(
            select(RecurringStream).where(
                or_(
                    RecurringStream.merchant_name.ilike(pattern),
                    RecurringStream.description.ilike(pattern),
                )
            )
        )
    ).all()
    for stream in streams:
        stream.hidden = True
