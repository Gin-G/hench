"""Xcel Energy (natural gas) bill, read from its reminder emails.

Xcel's account site sits behind reCAPTCHA Enterprise, so unlike Longmont it is
not signed into. Instead, Xcel emails "Reminder for your upcoming bill" from
noreply@account.xcelenergy.com with the amount and due date, and this reads
the newest one over read-only Gmail (services.google).

Gas bills swing with the season, which is the point: Plaid's detected stream
averages recent months and so badly understates a winter bill, while the
email carries the real figure. Between reminders — once a bill is paid and
before the next reminder lands — the next bill is projected a month on at the
last amount and flagged as an estimate.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Bill
from . import google
from .schedule import add_months
from .sources import (
    hide_detected_streams,
    paid_per_bank,
    parse_date,
    parse_money,
    value_after,
)

log = logging.getLogger("hench.xcel")

SOURCE = "xcel"
_QUERY = 'from:noreply@account.xcelenergy.com subject:"upcoming bill" newer_than:75d'
# How an Xcel payment appears in bank and card transactions.
_PAYMENT_PATTERN = "%xcel%"
# Gmail has no lockout to fear; this only keeps "Sync now" from re-reading.
_MIN_INTERVAL = timedelta(minutes=30)

# Most specific first, so "Last payment: $49.83" is never read as the bill.
_AMOUNT_LABELS = [
    re.compile(r"(amount|total|balance)\s+(amount\s+)?due", re.I),
    re.compile(r"\b(amount|total|balance)\b", re.I),
]
_DUE_LABELS = [
    re.compile(r"due\s+(date|on|by)", re.I),
    re.compile(r"\bdue\b", re.I),
]


def _first(lines: list[str], labels: list[re.Pattern], parse):
    for label in labels:
        found = value_after(lines, label, parse)
        if found is not None:
            return found
    return None


def parse_reminder(lines: list[str]):
    """(amount, due date) from a reminder email's text, either possibly None."""
    return _first(lines, _AMOUNT_LABELS, parse_money), _first(
        lines, _DUE_LABELS, parse_date
    )


async def sync_xcel(session: AsyncSession, force: bool = False) -> str:
    if not google.configured():
        return "not configured"

    bill = await session.scalar(select(Bill).where(Bill.source == SOURCE))
    now = datetime.now(timezone.utc)
    today = now.date()
    if bill and bill.source_attempted_at and not force:
        if now - bill.source_attempted_at < _MIN_INTERVAL:
            return "skipped: checked recently"
    if bill is None:
        bill = Bill(
            name="Xcel Energy (gas)",
            frequency="monthly",
            # Placeholder until the first email is read; never shown as due
            # while the bill is inactive.
            next_due_date=today,
            due_day=today.day,
            category="RENT_AND_UTILITIES",
            source=SOURCE,
            # Off the timeline until an email has actually been read.
            active=False,
        )
        session.add(bill)
    bill.source_attempted_at = now

    try:
        emails = await google.search(session, _QUERY, limit=3)
    except Exception as e:  # noqa: BLE001 - recorded on the bill, shown in the UI
        bill.source_error = str(e) or type(e).__name__
        await session.flush()
        return f"failed: {bill.source_error}"

    parsed = None
    for email in emails:
        amount, due = parse_reminder(email.lines)
        if amount is not None and due is not None:
            parsed = (email, amount, due)
            break

    if parsed is None:
        bill.source_error = (
            "No Xcel reminder email in the last 75 days"
            if not emails
            else "Found an Xcel email but could not read the amount and due date"
        )
        if emails:
            # Keep what was seen so the parser can be fixed without a re-read.
            bill.source_detail = {
                "email_subject": emails[0].subject,
                "email_received": emails[0].received.isoformat(),
                "lines": emails[0].lines[:80],
            }
        await session.flush()
        return f"failed: {bill.source_error}"

    email, amount, due = parsed
    paid_on = await paid_per_bank(
        session, _PAYMENT_PATTERN, email.received.date() - timedelta(days=3), amount
    )
    if paid_on or due < today - timedelta(days=3):
        # Paid, or long enough past due that the payment has surely gone out:
        # the next bill is about a month on, amount unknown until it is sent.
        bill.next_due_date = add_months(due, 1)
        while bill.next_due_date < today:
            bill.next_due_date = add_months(bill.next_due_date, 1)
        bill.due_date_estimated = True
        bill.last_paid_date = paid_on or bill.last_paid_date
    else:
        bill.next_due_date, bill.due_date_estimated = due, False
    bill.amount = amount
    bill.due_day = bill.next_due_date.day

    if not bill.active:
        # First successful read: from here the email, not Plaid's average,
        # speaks for this bill.
        await hide_detected_streams(session, _PAYMENT_PATTERN)
    bill.active = True
    bill.source_synced_at, bill.source_error = now, None
    bill.source_detail = {
        "email_subject": email.subject,
        "email_received": email.received.isoformat(),
        "amount": str(amount),
        "due_date": due.isoformat(),
        "paid_per_bank": paid_on.isoformat() if paid_on else None,
        "lines": email.lines[:80],
    }
    await session.flush()
    return f"ok: {amount} due {bill.next_due_date}" + (
        " (estimated)" if bill.due_date_estimated else ""
    )
