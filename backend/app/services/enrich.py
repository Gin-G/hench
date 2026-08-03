"""Forward-looking Plaid pulls: balances, liabilities, recurring streams.

These are separate from /transactions/sync because they are snapshots rather
than a cursor-driven log — each run replaces what came before for that Item.

Every fetch here is best-effort and individually guarded. An institution may
not support liabilities, and recurring detection needs roughly 90 days of
history before Plaid will return anything, so a failure in one of these must
never take down the transaction sync it runs alongside. The guard only wraps
the Plaid call, so a swallowed error cannot leave the session mid-statement.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import plaid
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.liabilities_get_request import LiabilitiesGetRequest
from plaid.model.transactions_recurring_get_request import (
    TransactionsRecurringGetRequest,
)
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account, Item, Liability, RecurringStream

log = logging.getLogger("hench.enrich")

# Plaid error codes that mean "this Item legitimately has nothing here",
# as opposed to something being wrong with the request.
_BENIGN = {
    "PRODUCTS_NOT_SUPPORTED",
    "PRODUCT_NOT_READY",
    "NO_LIABILITY_ACCOUNTS",
    "NO_ACCOUNTS",
    "INSUFFICIENT_CREDENTIALS",
}


def _plaid_error_code(exc: plaid.ApiException) -> str | None:
    try:
        return json.loads(exc.body).get("error_code")
    except (ValueError, TypeError, AttributeError):
        return None


def _money(value) -> Decimal | None:
    """Coerce a Plaid amount to Decimal without a float round trip.

    plaid-python hands back floats, so ``str()`` first: Decimal(0.1) is
    0.1000000000000000055511151231257827, Decimal("0.1") is not.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _rate(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def refresh_balances(session: AsyncSession, item: Item, access_token: str) -> int:
    """Snapshot current/available/limit onto each Account for this Item."""
    from ..plaid_client import get_plaid_client

    client = get_plaid_client()
    try:
        resp = client.accounts_balance_get(
            AccountsBalanceGetRequest(access_token=access_token)
        )
    except plaid.ApiException as exc:
        code = _plaid_error_code(exc)
        log.warning("balances unavailable for item=%s: %s", item.item_id, code)
        if code not in _BENIGN:
            raise
        return 0

    now = datetime.now(timezone.utc)
    n = 0
    for acct in resp.accounts:
        a = acct.to_dict()
        bal = a.get("balances") or {}
        result = await session.execute(
            update(Account)
            .where(Account.account_id == a["account_id"])
            .values(
                current_balance=_money(bal.get("current")),
                available_balance=_money(bal.get("available")),
                credit_limit=_money(bal.get("limit")),
                iso_currency_code=bal.get("iso_currency_code"),
                balances_updated_at=now,
            )
        )
        n += result.rowcount or 0
    log.info("refreshed balances item=%s accounts=%d", item.item_id, n)
    return n


def _purchase_apr(aprs: list[dict]) -> Decimal | None:
    """Pick the APR that payoff maths should use.

    Plaid returns one entry per APR type. Purchases are what a revolving
    balance actually accrues at, so prefer that; fall back to the only entry
    when a card reports just one.
    """
    for apr in aprs:
        if str(apr.get("apr_type", "")).lower() == "purchase_apr":
            return _rate(apr.get("apr_percentage"))
    if len(aprs) == 1:
        return _rate(aprs[0].get("apr_percentage"))
    return None


def _credit_values(acct: dict) -> dict:
    aprs = [a for a in (acct.get("aprs") or [])]
    return dict(
        liability_type="credit",
        next_payment_due_date=acct.get("next_payment_due_date"),
        minimum_payment_amount=_money(acct.get("minimum_payment_amount")),
        last_payment_amount=_money(acct.get("last_payment_amount")),
        last_payment_date=acct.get("last_payment_date"),
        is_overdue=acct.get("is_overdue"),
        last_statement_balance=_money(acct.get("last_statement_balance")),
        last_statement_issue_date=acct.get("last_statement_issue_date"),
        purchase_apr=_purchase_apr(aprs),
        aprs=json.loads(json.dumps(aprs, default=str)),
    )


def _student_values(acct: dict) -> dict:
    rate = acct.get("interest_rate_percentage")
    return dict(
        liability_type="student",
        next_payment_due_date=acct.get("next_payment_due_date"),
        minimum_payment_amount=_money(acct.get("minimum_payment_amount")),
        last_payment_amount=_money(acct.get("last_payment_amount")),
        last_payment_date=acct.get("last_payment_date"),
        is_overdue=acct.get("is_overdue"),
        interest_rate_percentage=_rate(rate),
        origination_principal_amount=_money(acct.get("origination_principal_amount")),
        origination_date=acct.get("origination_date"),
        outstanding_interest_amount=_money(acct.get("outstanding_interest_amount")),
        expected_payoff_date=acct.get("expected_payoff_date"),
        loan_status=str((acct.get("loan_status") or {}).get("type") or "") or None,
    )


def _mortgage_values(acct: dict) -> dict:
    rate = (acct.get("interest_rate") or {}).get("percentage")
    return dict(
        liability_type="mortgage",
        next_payment_due_date=acct.get("next_payment_due_date"),
        minimum_payment_amount=_money(acct.get("next_monthly_payment")),
        last_payment_amount=_money(acct.get("last_payment_amount")),
        last_payment_date=acct.get("last_payment_date"),
        is_overdue=acct.get("past_due_amount") not in (None, 0),
        interest_rate_percentage=_rate(rate),
        origination_principal_amount=_money(acct.get("origination_principal_amount")),
        origination_date=acct.get("origination_date"),
        expected_payoff_date=acct.get("maturity_date"),
    )


_BUILDERS = {
    "credit": _credit_values,
    "student": _student_values,
    "mortgage": _mortgage_values,
}


async def refresh_liabilities(
    session: AsyncSession, item: Item, access_token: str
) -> int:
    """Upsert credit/student/mortgage detail for this Item's accounts."""
    from ..plaid_client import get_plaid_client

    client = get_plaid_client()
    try:
        resp = client.liabilities_get(
            LiabilitiesGetRequest(access_token=access_token)
        )
    except plaid.ApiException as exc:
        code = _plaid_error_code(exc)
        log.warning("liabilities unavailable for item=%s: %s", item.item_id, code)
        if code not in _BENIGN:
            raise
        return 0

    liabilities = (resp.to_dict().get("liabilities") or {})
    # Only touch accounts we actually know about — a liability row for an
    # unknown account_id would violate the FK.
    known = set(
        (
            await session.scalars(
                select(Account.account_id).where(Account.item_id == item.item_id)
            )
        ).all()
    )

    n = 0
    for kind, builder in _BUILDERS.items():
        for acct in liabilities.get(kind) or []:
            account_id = acct.get("account_id")
            if account_id not in known:
                log.debug("skipping %s liability for unknown account", kind)
                continue
            values = builder(acct)
            values["account_id"] = account_id
            values["raw"] = json.loads(json.dumps(acct, default=str))
            stmt = pg_insert(Liability).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Liability.account_id],
                set_={k: getattr(stmt.excluded, k) for k in values if k != "account_id"},
            )
            await session.execute(stmt)
            n += 1

    log.info("refreshed liabilities item=%s rows=%d", item.item_id, n)
    return n


def _stream_values(stream: dict, item_id: str, direction: str) -> dict:
    avg = stream.get("average_amount") or {}
    last = stream.get("last_amount") or {}
    pfc = stream.get("personal_finance_category") or {}
    return dict(
        stream_id=stream["stream_id"],
        account_id=stream["account_id"],
        item_id=item_id,
        direction=direction,
        description=stream.get("description"),
        merchant_name=stream.get("merchant_name"),
        frequency=str(stream.get("frequency")) if stream.get("frequency") else None,
        average_amount=_money(avg.get("amount")),
        last_amount=_money(last.get("amount")),
        iso_currency_code=avg.get("iso_currency_code"),
        first_date=stream.get("first_date"),
        last_date=stream.get("last_date"),
        predicted_next_date=stream.get("predicted_next_date"),
        is_active=bool(stream.get("is_active", True)),
        status=str(stream.get("status")) if stream.get("status") else None,
        category_primary=pfc.get("primary"),
        category_detailed=pfc.get("detailed"),
    )


async def refresh_recurring(
    session: AsyncSession, item: Item, access_token: str
) -> int:
    """Upsert detected inflow (paydays) and outflow (bills) streams.

    Returns 0 quietly while Plaid still lacks the history to detect anything,
    which is the normal state for the first few months after linking.
    """
    from ..plaid_client import get_plaid_client

    client = get_plaid_client()
    account_ids = (
        await session.scalars(
            select(Account.account_id).where(Account.item_id == item.item_id)
        )
    ).all()
    if not account_ids:
        return 0

    try:
        resp = client.transactions_recurring_get(
            TransactionsRecurringGetRequest(
                access_token=access_token, account_ids=list(account_ids)
            )
        )
    except plaid.ApiException as exc:
        code = _plaid_error_code(exc)
        log.warning("recurring unavailable for item=%s: %s", item.item_id, code)
        if code not in _BENIGN:
            raise
        return 0

    data = resp.to_dict()
    n = 0
    for direction, key in (("inflow", "inflow_streams"), ("outflow", "outflow_streams")):
        for stream in data.get(key) or []:
            values = _stream_values(stream, item.item_id, direction)
            stmt = pg_insert(RecurringStream).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[RecurringStream.stream_id],
                set_={k: getattr(stmt.excluded, k) for k in values if k != "stream_id"},
            )
            await session.execute(stmt)
            n += 1

    log.info("refreshed recurring item=%s streams=%d", item.item_id, n)
    return n
