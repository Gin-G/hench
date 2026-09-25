"""Accounts with balances, and the recurring streams behind bills and paydays.

These are the read side of what services.enrich snapshots during sync, plus
the two edits the user makes on top of Plaid's data: account nicknames and
hiding a detected stream. Sync never writes either. Combining them into a
dated timeline is services.bills' job, not this router's.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Account, Item, RecurringStream
from ..schemas import (
    AccountOut,
    AccountUpdate,
    RecurringStreamOut,
    RecurringStreamUpdate,
)

router = APIRouter(tags=["accounts"])


def _account_out(account: Account, institution_name: str | None) -> AccountOut:
    return AccountOut.model_validate(
        {
            **{
                k: getattr(account, k)
                for k in AccountOut.model_fields
                if k not in ("institution_name", "liability")
            },
            "institution_name": institution_name,
            "liability": account.liability,
        }
    )


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    session: AsyncSession = Depends(get_session),
) -> list[AccountOut]:
    """Every linked account with its latest balance snapshot.

    ``current_balance`` follows Plaid's sign convention, which flips by
    account type: on a depository account it is what you hold, on a credit
    account it is what you owe.
    """
    rows = (
        await session.execute(
            select(Account, Item.institution_name)
            .join(Item, Account.item_id == Item.item_id)
            .options(selectinload(Account.liability))
            .order_by(Item.institution_name, Account.name)
        )
    ).all()

    return [_account_out(account, institution_name) for account, institution_name in rows]


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: str,
    body: AccountUpdate,
    session: AsyncSession = Depends(get_session),
) -> AccountOut:
    """Rename an account. Only the nickname is editable; the rest is Plaid's."""
    row = (
        await session.execute(
            select(Account, Item.institution_name)
            .join(Item, Account.item_id == Item.item_id)
            .options(selectinload(Account.liability))
            .where(Account.account_id == account_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account, institution_name = row
    account.nickname = (body.nickname or "").strip() or None
    await session.flush()
    return _account_out(account, institution_name)


@router.get("/recurring", response_model=list[RecurringStreamOut])
async def list_recurring(
    direction: str | None = Query(
        default=None,
        pattern="^(inflow|outflow)$",
        description="inflow for paydays, outflow for bills and subscriptions",
    ),
    active_only: bool = True,
    include_hidden: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[RecurringStream]:
    """Detected recurring streams.

    Empty until Plaid has enough history to detect anything — it needs roughly
    90 days and at least two occurrences of a stream, so a freshly linked Item
    returns nothing here for a while.
    """
    stmt = select(RecurringStream)
    if direction:
        stmt = stmt.where(RecurringStream.direction == direction)
    if active_only:
        stmt = stmt.where(RecurringStream.is_active.is_(True))
    if not include_hidden:
        stmt = stmt.where(RecurringStream.hidden.is_(False))
    stmt = stmt.order_by(
        RecurringStream.predicted_next_date.nulls_last(),
        RecurringStream.description,
    )
    return list((await session.scalars(stmt)).all())


@router.patch("/recurring/{stream_id}", response_model=RecurringStreamOut)
async def update_recurring(
    stream_id: str,
    body: RecurringStreamUpdate,
    session: AsyncSession = Depends(get_session),
) -> RecurringStream:
    """Hide or unhide a detected stream on the upcoming timeline."""
    stream = await session.get(RecurringStream, stream_id)
    if stream is None:
        raise HTTPException(status_code=404, detail="Recurring stream not found")
    stream.hidden = body.hidden
    await session.flush()
    await session.refresh(stream)
    return stream
