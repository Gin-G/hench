"""Transactions list (with filters) + inline category overrides."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import CategoryOverride, Transaction
from ..schemas import (
    OverrideRequest,
    TransactionList,
    TransactionOut,
)
from ..services.sankey import _month_bounds

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _to_out(txn: Transaction) -> TransactionOut:
    overridden = txn.override is not None
    if overridden:
        primary = txn.override.category_primary
        detailed = txn.override.category_detailed
    else:
        primary = txn.category_primary or txn.pfc_primary
        detailed = txn.category_detailed or txn.pfc_detailed
    return TransactionOut(
        transaction_id=txn.transaction_id,
        account_id=txn.account_id,
        date=txn.date,
        amount=float(txn.amount),
        name=txn.name,
        merchant_name=txn.merchant_name,
        pending=txn.pending,
        category_primary=primary,
        category_detailed=detailed,
        pfc_primary=txn.pfc_primary,
        pfc_detailed=txn.pfc_detailed,
        is_overridden=overridden,
    )


@router.get("", response_model=TransactionList)
async def list_transactions(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    start: date | None = None,
    end: date | None = None,
    account_id: str | None = None,
    category: str | None = Query(None, description="effective primary category"),
    search: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> TransactionList:
    if month:
        start, end = _month_bounds(month)

    conditions = []
    if start is not None:
        conditions.append(Transaction.date >= start)
    if end is not None:
        conditions.append(Transaction.date < end)
    if account_id:
        conditions.append(Transaction.account_id == account_id)
    if category:
        # Match against the effective category: override wins, else stored.
        conditions.append(
            or_(
                Transaction.category_primary == category,
                Transaction.pfc_primary == category,
            )
        )
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(Transaction.name.ilike(like), Transaction.merchant_name.ilike(like))
        )

    base = select(Transaction).where(*conditions)
    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    )
    txns = (
        await session.scalars(
            base.options(selectinload(Transaction.override))
            .order_by(Transaction.date.desc(), Transaction.transaction_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return TransactionList(
        total=total or 0, transactions=[_to_out(t) for t in txns]
    )


@router.put("/{transaction_id}/category", response_model=TransactionOut)
async def set_override(
    transaction_id: str,
    body: OverrideRequest,
    session: AsyncSession = Depends(get_session),
) -> TransactionOut:
    txn = await session.get(
        Transaction, transaction_id, options=[selectinload(Transaction.override)]
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="unknown transaction")
    if txn.override is None:
        txn.override = CategoryOverride(
            transaction_id=transaction_id,
            category_primary=body.category_primary,
            category_detailed=body.category_detailed,
        )
    else:
        txn.override.category_primary = body.category_primary
        txn.override.category_detailed = body.category_detailed
    await session.flush()
    return _to_out(txn)


@router.delete("/{transaction_id}/category", response_model=TransactionOut)
async def clear_override(
    transaction_id: str,
    session: AsyncSession = Depends(get_session),
) -> TransactionOut:
    txn = await session.get(
        Transaction, transaction_id, options=[selectinload(Transaction.override)]
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="unknown transaction")
    if txn.override is not None:
        await session.delete(txn.override)
        txn.override = None
    await session.flush()
    return _to_out(txn)
