"""Net worth, and the holdings entered by hand that go into it.

The figures are services.networth's; this router adds, edits and removes the
home, the car, retirement accounts and unlinked loans.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import Holding, HoldingValue
from ..schemas import HoldingIn, HoldingOut, HoldingUpdate, NetWorthResponse
from ..services.networth import build_networth

router = APIRouter(tags=["networth"])


@router.get("/networth", response_model=NetWorthResponse)
async def networth(session: AsyncSession = Depends(get_session)) -> NetWorthResponse:
    return await build_networth(session, date.today())


async def _holding(session: AsyncSession, holding_id: int) -> Holding:
    holding = await session.scalar(
        select(Holding).where(Holding.id == holding_id).options(selectinload(Holding.values))
    )
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found")
    return holding


async def _check_secured_by(
    session: AsyncSession, kind: str, secured_by_id: int | None, own_id: int | None = None
) -> None:
    if secured_by_id is None:
        return
    asset = await session.get(Holding, secured_by_id)
    if kind != "debt" or asset is None or asset.kind != "asset" or asset.id == own_id:
        raise HTTPException(
            status_code=422, detail="Only a debt can be secured, and only on an asset"
        )


def _record(holding: Holding, value, on: date) -> None:
    """Make value the figure at ``on``, replacing one already entered that day."""
    for v in holding.values:
        if v.as_of == on:
            v.value = value
            break
    else:
        holding.values.append(HoldingValue(as_of=on, value=value))
    latest = max(holding.values, key=lambda v: v.as_of)
    holding.value, holding.as_of = latest.value, latest.as_of


@router.post("/holdings", response_model=HoldingOut, status_code=201)
async def create_holding(
    body: HoldingIn, session: AsyncSession = Depends(get_session)
) -> Holding:
    await _check_secured_by(session, body.kind, body.secured_by_id)
    on = body.as_of or date.today()
    holding = Holding(
        name=body.name.strip(),
        kind=body.kind,
        category=body.category,
        value=body.value,
        as_of=on,
        secured_by_id=body.secured_by_id,
        notes=body.notes,
        values=[],
    )
    _record(holding, body.value, on)
    session.add(holding)
    await session.flush()
    return holding


@router.patch("/holdings/{holding_id}", response_model=HoldingOut)
async def update_holding(
    holding_id: int, body: HoldingUpdate, session: AsyncSession = Depends(get_session)
) -> Holding:
    """Rename or recategorise a holding, or give it a new value. A new value
    is kept alongside the old ones, dated as_of (default today)."""
    holding = await _holding(session, holding_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name"):
        holding.name = changes["name"].strip()
    if changes.get("category"):
        holding.category = changes["category"]
    if "notes" in changes:
        holding.notes = changes["notes"] or None
    if "secured_by_id" in changes:
        await _check_secured_by(session, holding.kind, body.secured_by_id, holding.id)
        holding.secured_by_id = body.secured_by_id
    if changes.get("value") is not None:
        _record(holding, body.value, body.as_of or date.today())
    await session.flush()
    return holding


@router.delete("/holdings/{holding_id}", status_code=204)
async def delete_holding(
    holding_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    holding = await _holding(session, holding_id)
    await session.delete(holding)
    await session.flush()
    return Response(status_code=204)
