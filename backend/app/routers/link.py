"""Plaid Link: token creation (incl. update mode), public_token exchange."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..crypto import decrypt, encrypt
from ..db import get_session
from ..models import Item
from ..plaid_client import get_plaid_client
from ..schemas import (
    CreateLinkTokenRequest,
    CreateLinkTokenResponse,
    ExchangePublicTokenRequest,
    ExchangePublicTokenResponse,
)
from ..services.sync import sync_item

log = logging.getLogger("hench.link")
router = APIRouter(prefix="/link", tags=["link"])


def _require_plaid_creds() -> None:
    settings = get_settings()
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise HTTPException(
            status_code=400,
            detail=(
                "Plaid credentials are not configured. Set PLAID_CLIENT_ID and "
                "PLAID_SECRET (copy .env.example to .env, then restart)."
            ),
        )


@router.post("/create_link_token", response_model=CreateLinkTokenResponse)
async def create_link_token(
    body: CreateLinkTokenRequest,
    session: AsyncSession = Depends(get_session),
) -> CreateLinkTokenResponse:
    _require_plaid_creds()
    settings = get_settings()
    client = get_plaid_client()

    kwargs = dict(
        user=LinkTokenCreateRequestUser(client_user_id="hench-single-user"),
        client_name=settings.link_name,
        country_codes=[CountryCode(c) for c in settings.plaid_country_codes],
        language="en",
    )
    if settings.plaid_webhook_url:
        kwargs["webhook"] = settings.plaid_webhook_url
    if settings.plaid_redirect_uri:
        kwargs["redirect_uri"] = settings.plaid_redirect_uri

    if body.item_id:
        # Update mode (ITEM_LOGIN_REQUIRED re-auth): pass the access_token and
        # omit products — Plaid re-opens Link for the existing Item.
        item = await session.get(Item, body.item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown item_id")
        kwargs["access_token"] = decrypt(item.access_token)
    else:
        kwargs["products"] = [Products(p) for p in settings.plaid_products]
        # Only valid on an initial link — update mode rejects it.
        if settings.plaid_additional_consented_products:
            kwargs["additional_consented_products"] = [
                Products(p) for p in settings.plaid_additional_consented_products
            ]

    request = LinkTokenCreateRequest(**kwargs)
    resp = client.link_token_create(request)
    return CreateLinkTokenResponse(
        link_token=resp.link_token,
        expiration=str(resp.expiration) if getattr(resp, "expiration", None) else None,
    )


@router.post("/exchange", response_model=ExchangePublicTokenResponse)
async def exchange_public_token(
    body: ExchangePublicTokenRequest,
    session: AsyncSession = Depends(get_session),
) -> ExchangePublicTokenResponse:
    _require_plaid_creds()
    client = get_plaid_client()
    settings = get_settings()

    exchange = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=body.public_token)
    )
    access_token = exchange.access_token
    item_id = exchange.item_id

    # Best-effort institution metadata.
    institution_id = None
    institution_name = None
    try:
        item_resp = client.item_get(ItemGetRequest(access_token=access_token))
        institution_id = item_resp.item.institution_id
        if institution_id:
            inst = client.institutions_get_by_id(
                InstitutionsGetByIdRequest(
                    institution_id=institution_id,
                    country_codes=[
                        CountryCode(c) for c in settings.plaid_country_codes
                    ],
                )
            )
            institution_name = inst.institution.name
    except Exception:  # noqa: BLE001 - metadata is non-critical
        log.warning("could not fetch institution metadata for item=%s", item_id)

    item = await session.get(Item, item_id)
    if item is None:
        item = Item(item_id=item_id, access_token=encrypt(access_token))
        session.add(item)
    else:
        # Re-link / update-mode completion: refresh the stored token.
        item.access_token = encrypt(access_token)
    item.institution_id = institution_id
    item.institution_name = institution_name
    item.status = "active"
    await session.flush()

    # Kick off an initial sync so the dashboard has data immediately.
    try:
        await sync_item(session, item)
    except Exception:  # noqa: BLE001
        log.exception("initial sync failed for item=%s", item_id)

    return ExchangePublicTokenResponse(
        item_id=item_id, institution_name=institution_name
    )
