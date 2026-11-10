"""Plaid /transactions/sync: cursor-based, idempotent, rule-applying.

Pulls added/modified/removed pages until ``has_more`` is false, upserting on
the Plaid natural keys so re-running is safe. Accounts are upserted from the
same response. Rules are applied to set the effective category.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_sync_request_options import (
    TransactionsSyncRequestOptions,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import tenancy, users
from ..crypto import legacy
from ..models import Account, Item, OAuthToken, Transaction, User
from ..plaid_client import get_plaid_client
from ..schemas import SyncResult
from . import enrich
from . import rules as rules_svc
from ..config import get_settings
from .longmont import sync_longmont
from .xcel import sync_xcel

log = logging.getLogger("hench.sync")


def _pfc(txn: dict) -> tuple[str | None, str | None, str | None]:
    pfc = txn.get("personal_finance_category") or {}
    return (
        pfc.get("primary"),
        pfc.get("detailed"),
        pfc.get("confidence_level"),
    )


async def _upsert_accounts(session: AsyncSession, item_id: str, accounts: list) -> None:
    for acct in accounts:
        a = acct.to_dict()
        stmt = pg_insert(Account).values(
            account_id=a["account_id"],
            item_id=item_id,
            name=a.get("name"),
            official_name=a.get("official_name"),
            mask=a.get("mask"),
            type=str(a.get("type")) if a.get("type") is not None else None,
            subtype=str(a.get("subtype")) if a.get("subtype") is not None else None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Account.account_id],
            set_={
                "name": stmt.excluded.name,
                "official_name": stmt.excluded.official_name,
                "mask": stmt.excluded.mask,
                "type": stmt.excluded.type,
                "subtype": stmt.excluded.subtype,
            },
        )
        await session.execute(stmt)


async def _upsert_transactions(
    session: AsyncSession,
    item_id: str,
    txns: list,
    compiled_rules: list[rules_svc.CompiledRule],
) -> int:
    n = 0
    for t in txns:
        txn = t.to_dict()
        pfc_primary, pfc_detailed, pfc_conf = _pfc(txn)
        cat_primary, cat_detailed = rules_svc.categorize(
            compiled_rules,
            merchant_name=txn.get("merchant_name"),
            name=txn.get("name"),
            pfc_primary=pfc_primary,
            pfc_detailed=pfc_detailed,
        )
        values = dict(
            transaction_id=txn["transaction_id"],
            account_id=txn["account_id"],
            item_id=item_id,
            amount=txn["amount"],
            iso_currency_code=txn.get("iso_currency_code"),
            date=txn["date"],
            authorized_date=txn.get("authorized_date"),
            name=txn.get("name"),
            merchant_name=txn.get("merchant_name"),
            pending=bool(txn.get("pending", False)),
            payment_channel=txn.get("payment_channel"),
            pfc_primary=pfc_primary,
            pfc_detailed=pfc_detailed,
            pfc_confidence=pfc_conf,
            category_primary=cat_primary,
            category_detailed=cat_detailed,
        )
        stmt = pg_insert(Transaction).values(**values)
        # On modify, refresh the synced fields but never clobber a manual
        # override (which lives in a separate table) — re-apply rules too.
        update_cols = {
            k: getattr(stmt.excluded, k)
            for k in values
            if k != "transaction_id"
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[Transaction.transaction_id],
            set_=update_cols,
        )
        await session.execute(stmt)
        n += 1
    return n


async def sync_item(session: AsyncSession, item: Item) -> SyncResult:
    """Run a full sync loop for a single Item. Commits via caller's session."""
    client = get_plaid_client()
    access_token = legacy(item.access_token)
    compiled_rules = await rules_svc.load_rules(session)

    cursor = item.transactions_cursor
    added = modified = removed = 0
    has_more = True

    while has_more:
        options = TransactionsSyncRequestOptions(
            include_personal_finance_category=True
        )
        req_kwargs = dict(access_token=access_token, options=options)
        if cursor:
            req_kwargs["cursor"] = cursor
        request = TransactionsSyncRequest(**req_kwargs)
        resp = client.transactions_sync(request)

        await _upsert_accounts(session, item.item_id, resp.accounts)
        added += await _upsert_transactions(
            session, item.item_id, resp.added, compiled_rules
        )
        modified += await _upsert_transactions(
            session, item.item_id, resp.modified, compiled_rules
        )

        removed_ids = [r.to_dict()["transaction_id"] for r in resp.removed]
        if removed_ids:
            existing = (
                await session.scalars(
                    select(Transaction).where(
                        Transaction.transaction_id.in_(removed_ids)
                    )
                )
            ).all()
            for txn in existing:
                await session.delete(txn)
            removed += len(existing)

        cursor = resp.next_cursor
        has_more = resp.has_more

    item.transactions_cursor = cursor
    item.status = "active"
    item.last_synced_at = datetime.now(timezone.utc)
    await session.flush()

    # Forward-looking snapshots. These run after the transaction upserts so
    # the accounts they key off definitely exist, and they are best-effort:
    # a bank that does not support liabilities must not fail the whole sync.
    await enrich.refresh_balances(session, item, access_token)
    await enrich.refresh_liabilities(session, item, access_token)
    await enrich.refresh_recurring(session, item, access_token)
    await session.flush()

    log.info(
        "synced item=%s added=%d modified=%d removed=%d",
        item.item_id,
        added,
        modified,
        removed,
    )
    return SyncResult(
        item_id=item.item_id, added=added, modified=modified, removed=removed
    )


async def _isolated(session: AsyncSession, what: str, work) -> object | None:
    """Run one unit of sync work and commit it, so one failure cannot discard
    the work of those that already succeeded."""
    try:
        result = await work()
        await session.commit()
        return result
    except Exception:  # noqa: BLE001 - one bad item shouldn't stop the rest
        log.exception("%s failed", what)
        # Without this the session stays in pending-rollback and everything
        # after fails on a PendingRollbackError rather than on its own.
        await session.rollback()
        return None


async def sync_user(session: AsyncSession, user: User) -> list[SyncResult]:
    """Every Item one user has linked, then the bills read from outside for
    them. Runs in that user's scope, with their key unlocked."""
    await users.unlock(session, user)
    results = []
    with tenancy.as_user(user.id):
        for item in (await session.scalars(select(Item))).all():
            result = await _isolated(
                session, f"sync of item={item.item_id}", lambda: sync_item(session, item)
            )
            if result is not None:
                results.append(result)

        # Bills read from outside: the Longmont portal login is the owner's;
        # a mailbox is read for whoever connected one.
        sources = []
        if user.email == get_settings().owner:
            sources.append(("longmont", sync_longmont))
        if await session.scalar(select(OAuthToken.provider).limit(1)) is not None:
            sources.append(("xcel", sync_xcel))
        for name, source in sources:
            status = await _isolated(
                session, f"{name} sync for user={user.id}", lambda: source(session)
            )
            log.info("%s user=%s: %s", name, user.id, status)
    return results


async def sync_all(session: AsyncSession) -> list[SyncResult]:
    """Every user's Items, each in its own user's scope."""
    with tenancy.as_system():
        everyone = (await session.scalars(select(User).order_by(User.id))).all()
    results = []
    for user in everyone:
        try:
            results += await sync_user(session, user)
        except Exception:  # noqa: BLE001 - say, their key could not be unlocked
            log.exception("sync failed for user=%s", user.id)
            await session.rollback()
    return results
