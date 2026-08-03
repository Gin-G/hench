"""Accounts with balances, and the recurring streams behind bills and paydays.

These are the read side of what services.enrich snapshots during sync. They
are deliberately plain listings — the forecast that combines them into a dated
timeline is a separate concern and does not belong here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Account, Item, RecurringStream
from ..schemas import AccountOut, RecurringStreamOut

router = APIRouter(tags=["accounts"])


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

    return [
        AccountOut.model_validate(
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
        for account, institution_name in rows
    ]


@router.get("/recurring", response_model=list[RecurringStreamOut])
async def list_recurring(
    direction: str | None = Query(
        default=None,
        pattern="^(inflow|outflow)$",
        description="inflow for paydays, outflow for bills and subscriptions",
    ),
    active_only: bool = True,
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
    stmt = stmt.order_by(
        RecurringStream.predicted_next_date.nulls_last(),
        RecurringStream.description,
    )
    return list((await session.scalars(stmt)).all())
