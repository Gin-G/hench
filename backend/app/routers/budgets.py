"""Monthly budgets and the push alerts on charges against them."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Budget
from ..schemas import AlertSettings, AlertSettingsIn, BudgetIn, BudgetsResponse, BudgetUpdate
from ..services import notify
from ..services.budgets import alert_settings, build_budgets

router = APIRouter(tags=["budgets"])


@router.get("/budgets", response_model=BudgetsResponse)
async def budgets(request: Request, session: AsyncSession = Depends(get_session)) -> BudgetsResponse:
    """This month's budgets with what is spent and left, the categories one
    can be set on with what they have averaged, and the alert settings."""
    # A topic made here for the alert settings is saved with the session.
    return await build_budgets(session, request.state.user, date.today())


async def _budget(session: AsyncSession, budget_id: int) -> Budget:
    budget = await session.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    return budget


@router.post("/budgets", status_code=201)
async def create_budget(body: BudgetIn, session: AsyncSession = Depends(get_session)) -> dict:
    item = body.item or None
    exists = await session.scalar(
        select(Budget.id).where(
            Budget.group == body.group,
            Budget.item.is_(None) if item is None else Budget.item == item,
        )
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="There is a budget for that already")
    budget = Budget(group=body.group, item=item, amount=body.amount)
    session.add(budget)
    await session.flush()
    return {"id": budget.id}


@router.patch("/budgets/{budget_id}", status_code=204)
async def update_budget(
    budget_id: int, body: BudgetUpdate, session: AsyncSession = Depends(get_session)
) -> Response:
    (await _budget(session, budget_id)).amount = body.amount
    return Response(status_code=204)


@router.delete("/budgets/{budget_id}", status_code=204)
async def delete_budget(budget_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    await session.delete(await _budget(session, budget_id))
    return Response(status_code=204)


@router.put("/budgets/alerts", response_model=AlertSettings)
async def set_alerts(
    body: AlertSettingsIn, request: Request, session: AsyncSession = Depends(get_session)
) -> AlertSettings:
    request.state.user.alerts_enabled = body.enabled
    return alert_settings(request.state.user)


@router.post("/budgets/alerts/test", status_code=204)
async def test_alert(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    """Push a test notification, to check the phone is subscribed."""
    try:
        await notify.send(
            request.state.user,
            "hench is connected",
            "Budget alerts will arrive here after each sync.",
            tags=["white_check_mark"],
        )
    except notify.NotifyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Response(status_code=204)
