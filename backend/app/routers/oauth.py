"""Connecting read-only Gmail, once, through the browser.

``/oauth/google/start`` sends the browser to Google's consent screen and
``/oauth/google/callback`` stores the grant it comes back with. Both sit
behind Cloudflare Access like every other route, so only the allow-listed
user can complete a connection. The ``state`` round trip is additionally
bound to that browser by a cookie, so a callback URL crafted elsewhere
cannot attach someone else's mailbox.

The browser reaches these under /api/..., so redirects and cookie paths are
written with that prefix even though the routes are served at root.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import OAuthToken
from ..services import google

log = logging.getLogger("hench.oauth")
router = APIRouter(prefix="/oauth/google", tags=["oauth"])

_STATE_COOKIE = "hench_google_state"
# Where the browser lands afterwards: the app, on the bills tab.
_BACK = "/?view=bills"


class GoogleStatus(BaseModel):
    configured: bool
    connected: bool
    account_email: str | None = None


@router.get("/status", response_model=GoogleStatus)
async def status(session: AsyncSession = Depends(get_session)) -> GoogleStatus:
    row = await session.get(OAuthToken, google.PROVIDER)
    return GoogleStatus(
        configured=google.configured(),
        connected=row is not None,
        account_email=row.account_email if row else None,
    )


@router.get("/start")
async def start() -> RedirectResponse:
    if not google.configured():
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(google.auth_url(state), status_code=302)
    # Lax, not Strict: the return trip from Google is a top-level navigation
    # from another site, which Strict would strip the cookie from.
    resp.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/oauth/google",
    )
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    expected = request.cookies.get(_STATE_COOKIE)
    if not expected or not secrets.compare_digest(expected, state):
        raise HTTPException(status_code=400, detail="OAuth state mismatch; start again")
    if error or not code:
        resp = RedirectResponse(f"{_BACK}&gmail=declined", status_code=302)
    else:
        try:
            email = await google.connect(session, code)
            log.info("gmail connected for %s", email)
            resp = RedirectResponse(f"{_BACK}&gmail=connected", status_code=302)
        except google.GoogleAuthError as e:
            log.warning("gmail connect failed: %s", e)
            resp = RedirectResponse(f"{_BACK}&gmail=failed", status_code=302)
    resp.delete_cookie(_STATE_COOKIE, path="/api/oauth/google")
    return resp


@router.delete("", status_code=204)
async def disconnect(session: AsyncSession = Depends(get_session)) -> Response:
    """Revoke the grant at Google and forget it here."""
    await google.disconnect(session)
    return Response(status_code=204)
