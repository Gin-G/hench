"""Monthly Sankey cash-flow data + helpers for the month/category selectors."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import CategoryOverride, Transaction
from ..schemas import SankeyResponse
from ..services.sankey import build_sankey

router = APIRouter(tags=["sankey"])


@router.get("/sankey", response_model=SankeyResponse)
async def sankey(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    session: AsyncSession = Depends(get_session),
) -> SankeyResponse:
    if month is None:
        month = date.today().strftime("%Y-%m")
    return await build_sankey(session, month)


@router.get("/sankey/months", response_model=list[str])
async def available_months(
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """Distinct 'YYYY-MM' that have transactions, newest first."""
    col = func.to_char(Transaction.date, "YYYY-MM")
    rows = (
        await session.scalars(select(distinct(col)).order_by(col.desc()))
    ).all()
    return list(rows)


@router.get("/categories", response_model=list[str])
async def categories(session: AsyncSession = Depends(get_session)) -> list[str]:
    """Distinct effective primary categories seen, for the override dropdown."""
    seen: set[str] = set()
    for col in (
        Transaction.category_primary,
        Transaction.pfc_primary,
        CategoryOverride.category_primary,
    ):
        rows = (await session.scalars(select(distinct(col)))).all()
        seen.update(r for r in rows if r)
    return sorted(seen)
