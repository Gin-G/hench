"""Date arithmetic for repeating due dates.

Pure functions, no database, so the projection rules live in one place for both
manual bills and Plaid's recurring streams.
"""
from __future__ import annotations

import calendar
from collections.abc import Iterator
from datetime import date, timedelta

# Manual bill frequencies, as the UI offers them.
BILL_FREQUENCIES = ("once", "weekly", "biweekly", "monthly", "quarterly", "annually")

# Plaid recurring-stream frequency -> the bill frequency that projects it.
# SEMI_MONTHLY has no exact equivalent; it is handled separately below.
PLAID_FREQUENCIES = {
    "WEEKLY": "weekly",
    "BIWEEKLY": "biweekly",
    "MONTHLY": "monthly",
    "ANNUALLY": "annually",
}

_MONTHS = {"monthly": 1, "quarterly": 3, "annually": 12}


def add_months(d: date, months: int, day: int | None = None) -> date:
    """``d`` moved by ``months``, landing on ``day`` (default ``d.day``).

    The day is clamped to the length of the target month, so the 31st becomes
    the 30th in April — but because the caller passes the original ``day``
    each time, the next step goes back to the 31st rather than staying short.
    """
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day or d.day, last))


def advance(d: date, frequency: str, day: int | None = None) -> date | None:
    """The occurrence after ``d``, or None for a one-off."""
    if frequency == "weekly":
        return d + timedelta(weeks=1)
    if frequency == "biweekly":
        return d + timedelta(weeks=2)
    if frequency == "semi_monthly":
        # Plaid does not say which two days; alternating 15-day steps keeps a
        # projection roughly twice a month without claiming more precision.
        return d + timedelta(days=15)
    if frequency in _MONTHS:
        return add_months(d, _MONTHS[frequency], day)
    return None


def occurrences(
    start: date, frequency: str, until: date, day: int | None = None
) -> Iterator[date]:
    """``start`` and every later occurrence on or before ``until``."""
    current: date | None = start
    while current is not None and current <= until:
        yield current
        current = advance(current, frequency, day)


def next_on_or_after(
    start: date, frequency: str, today: date, day: int | None = None
) -> date:
    """The first occurrence from ``start`` that is not before ``today``.

    A one-off already past returns ``start`` unchanged.
    """
    current = start
    while current < today:
        following = advance(current, frequency, day)
        if following is None:
            break
        current = following
    return current
