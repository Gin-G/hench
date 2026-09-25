"""City of Longmont utility bill, read from the customer portal.

myutilityaccount.longmontcolorado.gov has no API, so this signs in with the
account's own login (OpenBao ``hench/longmont``) and reads the dashboard: the
account balance, past-due amount and recent activity. The due date is shown
only while a bill is open; when the portal does not state one, it is projected
from how long after each past bill the payment went out, and flagged as an
estimate.

Care is taken not to get the account locked: at most one login per
``_MIN_INTERVAL``, one attempt per run, no retries, and a rejected password
backs off for a day.

The parsing functions are pure and work on saved HTML, so a portal change can
be diagnosed from a page capture without logging in again.
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import logging
import re
import ssl
import statistics
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Bill, RecurringStream, Transaction
from .schedule import add_months

log = logging.getLogger("hench.longmont")

SOURCE = "longmont"
BASE = "https://myutilityaccount.longmontcolorado.gov"
_CA = Path(__file__).resolve().parent.parent / "certs" / "sectigo_dv_r36.pem"

_MIN_INTERVAL = timedelta(hours=6)
_REJECTED_BACKOFF = timedelta(hours=24)
# Used only until there is a paid bill to learn the gap from.
_DEFAULT_PAY_GAP_DAYS = 21
# How a Longmont utility payment appears in bank and card transactions.
_PAYMENT_PATTERN = "%longmont util%"


class LoginRejected(Exception):
    """The portal refused the username or password."""


@dataclass
class Activity:
    on: date
    description: str
    amount: Decimal  # positive = charge, negative = payment

    @property
    def is_bill(self) -> bool:
        return "bill" in self.description.lower() and self.amount > 0


@dataclass
class Snapshot:
    balance: Decimal
    past_due: Decimal
    activity: list[Activity]
    due_date: date | None = None
    # Visible text of the "My Bill" widget, kept for diagnosing a missed parse.
    bill_widget: list[str] = field(default_factory=list)


# --- Parsing ----------------------------------------------------------------
_MONEY = re.compile(r"(-?)\$\s*(-?[\d,]+\.\d{2})")


def _money(text: str) -> Decimal | None:
    m = _MONEY.search(text)
    if not m:
        return None
    value = Decimal(m.group(2).replace(",", ""))
    return -abs(value) if m.group(1) or value < 0 else value


def _parse_date(text: str) -> date | None:
    text = text.strip()
    for pattern, fmt in (
        (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
        (r"\d{1,2}/\d{1,2}/\d{4}", "%m/%d/%Y"),
        (r"[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}", None),
    ):
        m = re.search(pattern, text)
        if not m:
            continue
        found = m.group(0).replace(".", "")
        if fmt:
            return datetime.strptime(found, fmt).date()
        for f in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(found, f).date()
            except ValueError:
                pass
    return None


def _element_text(html: str, element_id: str) -> str | None:
    """Text of the first element with this id, up to its closing tag."""
    m = re.search(
        rf"id=['\"]{re.escape(element_id)}['\"][^>]*>(.*?)</(?:div|span|td)>",
        html,
        re.S,
    )
    return unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else None


class _TableRows(HTMLParser):
    """Rows of the table with the given id, as lists of cell text."""

    def __init__(self, table_id: str):
        super().__init__()
        self.table_id, self.rows = table_id, []
        self._depth = 0  # >0 while inside the target table
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._depth or dict(attrs).get("id") == self.table_id:
                self._depth += 1
        elif self._depth and tag == "tr":
            self._row = []
        elif self._depth and tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
        elif tag == "td" and self._cell is not None:
            self._row.append(" ".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None and data.strip():
            self._cell.append(data.strip())


def visible_lines(html: str) -> list[str]:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    lines = []
    for line in re.sub(r"<[^>]+>", "\n", html).split("\n"):
        line = re.sub(r"\s+", " ", unescape(line)).strip()
        if line:
            lines.append(line)
    return lines


def parse_dashboard(html: str) -> Snapshot:
    balance_text = _element_text(html, "showTotalBalanceFromAccExt")
    if balance_text is None or _money(balance_text) is None:
        raise ValueError("total balance not found on the dashboard")
    past_due_text = _element_text(
        html, "myAccounts_showPastDueAmountDueDateOnDashboardWidget"
    )

    table = _TableRows("tranxTable")
    table.feed(html)
    activity = []
    for cells in table.rows:
        if len(cells) < 3:
            continue
        on, amount = _parse_date(cells[0]), _money(cells[2])
        if on and amount is not None:
            activity.append(Activity(on, cells[1], amount))
    activity.sort(key=lambda a: a.on, reverse=True)

    return Snapshot(
        balance=_money(balance_text),
        past_due=(_money(past_due_text or "") or Decimal("0")),
        activity=activity,
    )


def parse_due_date(widget_html: str) -> tuple[date | None, list[str]]:
    """A due date from the "My Bill" widget, if it states one.

    The widget's layout has not been seen with a bill open, so this looks for
    a date on or just after any line mentioning "due" rather than a fixed id.
    """
    lines = visible_lines(widget_html)
    for i, line in enumerate(lines):
        if "due" in line.lower() and "past due" not in line.lower():
            for candidate in lines[i : i + 3]:
                found = _parse_date(candidate)
                if found:
                    return found, lines
    return None, lines


# --- Fetching ---------------------------------------------------------------
def _fetch_sync(username: str, password: str) -> Snapshot:
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=str(_CA))
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) hench")]

    def get(path: str, data: dict | None = None) -> tuple[str, str]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        with opener.open(BASE + path, body, timeout=30) as r:
            return r.geturl(), r.read().decode("utf-8", "replace")

    _, login_page = get("/app/login.jsp")
    token = re.search(r'name="jspCSRFToken" value="([^"]+)"', login_page)
    if not token:
        raise ValueError("login form not found — has the portal changed?")
    url, dashboard = get(
        "/app/capricorn?para=index",
        {
            "jspCSRFToken": token.group(1),
            "accessCode": username,
            "password": password,
            "nextPara": "",
            "nextPara_attr1": "",
        },
    )
    if 'id="login-form"' in dashboard:
        raise LoginRejected("the portal rejected the username or password")

    try:
        snapshot = parse_dashboard(dashboard)
        _, widget = get("/app/capricorn?para=ajaxMyCurrentBill")
        snapshot.due_date, snapshot.bill_widget = parse_due_date(widget)
        return snapshot
    finally:
        try:
            get("/app/capricorn?para=logoff")
        except Exception:  # noqa: BLE001 - logging off is a courtesy
            pass


# --- Applying to the Bill ---------------------------------------------------
def _pay_gap_days(activity: list[Activity]) -> int:
    """Median days from each bill to the first payment after it."""
    ordered = sorted(activity, key=lambda a: a.on)
    gaps = []
    for i, bill in enumerate(ordered):
        if not bill.is_bill:
            continue
        paid = next((a for a in ordered[i + 1 :] if a.amount < 0), None)
        if paid:
            gaps.append((paid.on - bill.on).days)
    return int(statistics.median(gaps)) if gaps else _DEFAULT_PAY_GAP_DAYS


async def _paid_per_bank(session: AsyncSession, since: date, amount: Decimal) -> date | None:
    """Date of a bank or card payment to Longmont matching the open bill.

    The portal can take days to post a payment; Plaid often sees it first.
    """
    rows = (
        await session.execute(
            select(Transaction.date, Transaction.amount).where(
                or_(
                    Transaction.name.ilike(_PAYMENT_PATTERN),
                    Transaction.merchant_name.ilike(_PAYMENT_PATTERN),
                ),
                Transaction.date >= since,
            )
        )
    ).all()
    for on, paid in rows:
        if abs(Decimal(paid) - amount) <= Decimal("1.00"):
            return on
    return None


async def apply_snapshot(session: AsyncSession, bill: Bill, snap: Snapshot, today: date) -> None:
    last_bill = next((a for a in snap.activity if a.is_bill), None)
    gap = _pay_gap_days(snap.activity)
    paid_on = None
    if snap.balance > 0 and last_bill:
        paid_on = await _paid_per_bank(session, last_bill.on, snap.balance)

    if snap.balance > 0 and not paid_on:
        # An open bill: what is owed, by the stated date or an estimate.
        bill.amount = snap.balance
        if snap.due_date:
            bill.next_due_date, bill.due_date_estimated = snap.due_date, False
        else:
            anchor = last_bill.on if last_bill else today
            bill.next_due_date = anchor + timedelta(days=gap)
            bill.due_date_estimated = True
    else:
        # Nothing owed (or paid and not yet posted): the next bill is expected
        # a month after the last one, for roughly the same amount.
        anchor = last_bill.on if last_bill else today
        next_bill = add_months(anchor, 1)
        while next_bill < today - timedelta(days=gap):
            next_bill = add_months(next_bill, 1)
        bill.amount = last_bill.amount if last_bill else bill.amount
        bill.next_due_date = next_bill + timedelta(days=gap)
        bill.due_date_estimated = True
    if paid_on:
        bill.last_paid_date = paid_on
    elif payments := [a for a in snap.activity if a.amount < 0]:
        bill.last_paid_date = payments[0].on
    bill.due_day = bill.next_due_date.day

    bill.source_detail = {
        "balance": str(snap.balance),
        "past_due": str(snap.past_due),
        "due_date": snap.due_date.isoformat() if snap.due_date else None,
        "paid_per_bank": paid_on.isoformat() if paid_on else None,
        "pay_gap_days": gap,
        "last_bill": (
            {"date": last_bill.on.isoformat(), "amount": str(last_bill.amount)}
            if last_bill
            else None
        ),
        "activity": [
            {"date": a.on.isoformat(), "description": a.description, "amount": str(a.amount)}
            for a in snap.activity[:12]
        ],
        "bill_widget": snap.bill_widget[:60],
    }


async def _hide_detected_stream(session: AsyncSession) -> None:
    """Hide Plaid's own guess at this bill so it is not counted twice."""
    streams = (
        await session.scalars(
            select(RecurringStream).where(
                or_(
                    RecurringStream.merchant_name.ilike(_PAYMENT_PATTERN),
                    RecurringStream.description.ilike(_PAYMENT_PATTERN),
                )
            )
        )
    ).all()
    for stream in streams:
        stream.hidden = True


async def sync_longmont(session: AsyncSession, force: bool = False) -> str:
    """Refresh the Longmont bill. Returns a one-line status for logs."""
    settings = get_settings()
    if not (settings.longmont_username and settings.longmont_password):
        return "not configured"

    bill = await session.scalar(select(Bill).where(Bill.source == SOURCE))
    now = datetime.now(timezone.utc)
    if bill and bill.source_attempted_at and not force:
        wait = _REJECTED_BACKOFF if bill.source_error and "rejected" in bill.source_error else _MIN_INTERVAL
        if now - bill.source_attempted_at < wait:
            return f"skipped: last attempt {bill.source_attempted_at:%Y-%m-%d %H:%M} UTC"

    if bill is None:
        bill = Bill(
            name="City of Longmont Utilities",
            frequency="monthly",
            next_due_date=now.date(),
            due_day=now.day,
            category="RENT_AND_UTILITIES",
            source=SOURCE,
            # Kept off the timeline until a fetch succeeds, rather than
            # showing a placeholder date with no amount.
            active=False,
        )
        session.add(bill)
        await _hide_detected_stream(session)

    bill.source_attempted_at = now
    try:
        snap = await asyncio.to_thread(
            _fetch_sync, settings.longmont_username, settings.longmont_password
        )
    except Exception as e:  # noqa: BLE001 - recorded on the bill, shown in the UI
        bill.source_error = str(e) or type(e).__name__
        log.warning("longmont fetch failed: %s", bill.source_error)
        await session.flush()
        return f"failed: {bill.source_error}"

    await apply_snapshot(session, bill, snap, now.date())
    bill.source_synced_at, bill.source_error, bill.active = now, None, True
    await session.flush()
    return (
        f"ok: balance {snap.balance}, next due {bill.next_due_date}"
        f"{' (estimated)' if bill.due_date_estimated else ''}"
    )
