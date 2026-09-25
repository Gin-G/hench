"""Hand-entered bills, plus the merged upcoming-payments and debt views.

``/bills`` is plain CRUD over what Plaid cannot see. ``/upcoming`` and
``/debts`` are read-only and combine those bills with Plaid liabilities and
recurring streams — see services.bills.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Bill
from ..schemas import (
    BillIn,
    BillOut,
    BillUpdate,
    DebtsResponse,
    MarkPaidRequest,
    PlanResponse,
    UpcomingResponse,
)
from ..services.bills import build_debts, build_upcoming, effective_due
from ..services.plan import build_plan
from ..services.schedule import advance

router = APIRouter(tags=["bills"])


async def _get_bill(session: AsyncSession, bill_id: int) -> Bill:
    bill = await session.get(Bill, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    return bill


@router.get("/bills", response_model=list[BillOut])
async def list_bills(
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[BillOut]:
    """Hand-entered bills, with autopay due dates rolled past today.

    Nothing marks an autopay bill paid, so its stored date falls behind; the
    listing shows the same next date as ``/upcoming`` does instead.
    """
    stmt = select(Bill)
    if not include_inactive:
        # A synced bill stays inactive until its first successful read; list
        # it anyway so the reason it has not appeared (its error) is visible.
        stmt = stmt.where(or_(Bill.active.is_(True), Bill.source.is_not(None)))
    today = date.today()
    out = [
        BillOut.model_validate(bill).model_copy(
            update={"next_due_date": effective_due(bill, today)}
        )
        for bill in (await session.scalars(stmt)).all()
    ]
    return sorted(out, key=lambda b: (b.next_due_date, b.name.lower()))


@router.post("/bills", response_model=BillOut, status_code=201)
async def create_bill(
    body: BillIn, session: AsyncSession = Depends(get_session)
) -> Bill:
    bill = Bill(**body.model_dump(), due_day=body.next_due_date.day)
    session.add(bill)
    await session.flush()
    await session.refresh(bill)
    return bill


@router.patch("/bills/{bill_id}", response_model=BillOut)
async def update_bill(
    bill_id: int, body: BillUpdate, session: AsyncSession = Depends(get_session)
) -> Bill:
    bill = await _get_bill(session, bill_id)
    changes = body.model_dump(exclude_unset=True)
    # Required columns cannot be cleared, only changed.
    for field in ("name", "frequency", "next_due_date", "autopay", "active"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    for field, value in changes.items():
        setattr(bill, field, value)
    if "next_due_date" in changes:
        # Moving the due date by hand re-anchors the day of month too.
        bill.due_day = bill.next_due_date.day
    await session.flush()
    await session.refresh(bill)
    return bill


@router.delete("/bills/{bill_id}", status_code=204)
async def delete_bill(
    bill_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    await session.delete(await _get_bill(session, bill_id))
    return Response(status_code=204)


@router.post("/bills/{bill_id}/paid", response_model=BillOut)
async def mark_bill_paid(
    bill_id: int,
    body: MarkPaidRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> Bill:
    """Record a payment and roll the bill to its next due date.

    Advances one occurrence at a time, so a bill missed twice needs marking
    twice. A one-off bill is deactivated instead. ``balance`` is left alone:
    the split between principal and interest is the lender's, not ours.
    """
    bill = await _get_bill(session, bill_id)
    if bill.source:
        raise HTTPException(
            status_code=409,
            detail=f"{bill.name} is kept up to date from {bill.source}; "
            "it clears when the payment posts there",
        )
    bill.last_paid_date = (body.paid_on if body else None) or date.today()
    following = advance(bill.next_due_date, bill.frequency, bill.due_day)
    if following is None:
        bill.active = False
    else:
        bill.next_due_date = following
    await session.flush()
    await session.refresh(bill)
    return bill


@router.get("/upcoming", response_model=UpcomingResponse)
async def upcoming(
    days: int = Query(30, ge=1, le=366),
    session: AsyncSession = Depends(get_session),
) -> UpcomingResponse:
    """Every payment due between today and ``days`` from now, oldest first.

    Overdue bills and cards are included ahead of today; paid cards are kept
    but flagged, and left out of ``total_due``.
    """
    return await build_upcoming(session, date.today(), days)


@router.get("/debts", response_model=DebtsResponse)
async def debts(session: AsyncSession = Depends(get_session)) -> DebtsResponse:
    """Plaid credit and loan accounts, plus bills carrying a balance."""
    return await build_debts(session, date.today())


@router.get("/plan", response_model=PlanResponse)
async def plan(session: AsyncSession = Depends(get_session)) -> PlanResponse:
    """Checking balance less everything due before the next paycheck."""
    return await build_plan(session, date.today())
