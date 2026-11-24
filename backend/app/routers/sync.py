"""Sync triggers: Plaid webhook receiver + manual trigger; item listing.

A manual sync is the signed-in user's own Items. A webhook carries no user,
so the Item it names is looked up across users and synced as its owner.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import tenancy, users
from ..db import get_session
from ..models import Item, User
from ..schemas import ItemOut, SyncResult
from ..services.sync import alert_safely, sync_item, sync_user

log = logging.getLogger("hench.sync")
router = APIRouter(tags=["sync"])


@router.get("/items", response_model=list[ItemOut])
async def list_items(session: AsyncSession = Depends(get_session)) -> list[Item]:
    return list((await session.scalars(select(Item))).all())


@router.post("/sync", response_model=list[SyncResult])
async def trigger_sync(
    request: Request,
    item_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[SyncResult]:
    """Manual sync — all your items, or a single item if ``item_id`` is given."""
    if item_id:
        item = await session.get(Item, item_id)
        if item is None:
            return []
        return [await sync_item(session, item)]
    return await sync_user(session, request.state.user)


@router.post("/webhook")
async def plaid_webhook(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """Receive Plaid webhooks.

    NOTE: webhook JWT verification (the ``Plaid-Verification`` header) is not
    implemented — acceptable for a network-internal homelab deployment. Add
    verification here before exposing this endpoint to the open internet.
    """
    body = await request.json()
    webhook_type = body.get("webhook_type")
    webhook_code = body.get("webhook_code")
    item_id = body.get("item_id")
    log.info("webhook type=%s code=%s item=%s", webhook_type, webhook_code, item_id)

    if not item_id:
        return {"status": "ignored"}

    with tenancy.as_system():
        owner_id = await session.scalar(select(Item.user_id).where(Item.item_id == item_id))
        owner = await session.get(User, owner_id) if owner_id is not None else None
    if owner is None:
        log.warning("webhook for unknown item=%s", item_id)
        return {"status": "unknown_item"}
    await users.unlock(session, owner)
    with tenancy.as_user(owner.id):
        return await _handle_webhook(session, owner, body, item_id)


async def _handle_webhook(
    session: AsyncSession, owner: User, body: dict, item_id: str
) -> dict:
    webhook_type = body.get("webhook_type")
    webhook_code = body.get("webhook_code")
    item = await session.get(Item, item_id)

    if webhook_type == "TRANSACTIONS" and webhook_code == "SYNC_UPDATES_AVAILABLE":
        result = await sync_item(session, item)
        await alert_safely(session, owner)
        return {"status": "synced", "result": result.model_dump()}

    # Item needs re-auth — flag it so the UI can offer Link update mode.
    error_code = (body.get("error") or {}).get("error_code")
    if webhook_type == "ITEM" and (
        error_code == "ITEM_LOGIN_REQUIRED" or webhook_code == "PENDING_EXPIRATION"
    ):
        item.status = "login_required"
        await session.flush()
        return {"status": "login_required"}

    return {"status": "ok"}
