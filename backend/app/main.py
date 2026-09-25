"""FastAPI application entrypoint.

Routes are mounted at root (no ``/api`` prefix): in the cluster the Traefik
``strip-api`` middleware rewrites ``/api/*`` -> ``/*`` before traffic reaches
this service, and the Vite dev proxy does the same locally.

Every route except /health requires a valid Cloudflare Access token for an
allow-listed email; see access.py.
"""
from __future__ import annotations

import json
import logging

import plaid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import access
from .config import get_settings
from .routers import accounts, bills, link, oauth, sankey, sync, transactions

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())

log = logging.getLogger("hench")

app = FastAPI(title="hench", version="0.1.0")

if settings.cf_access_enabled:
    access.install(app, settings)
elif settings.plaid_env == "production":
    # Real bank data with no auth in front of it is never an acceptable
    # fallback, so a missing setting fails the rollout instead.
    raise RuntimeError(
        "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD must be set when PLAID_ENV=production"
    )
else:
    log.warning("Cloudflare Access verification is off (non-production dev mode)")


@app.exception_handler(plaid.ApiException)
async def plaid_exception_handler(
    request: Request, exc: plaid.ApiException
) -> JSONResponse:
    """Surface Plaid's own error_code instead of an opaque 500.

    Plaid puts the useful part in the response body, so an unhandled
    ApiException reaches the UI as a bare 500 with nothing actionable in it —
    which is exactly how an INVALID_API_KEYS misconfiguration stayed invisible
    until someone read the pod logs.
    """
    try:
        body = json.loads(exc.body)
    except (ValueError, TypeError):
        body = {}
    error_code = body.get("error_code")
    log.warning(
        "plaid error on %s %s: %s %s",
        request.method,
        request.url.path,
        error_code,
        body.get("error_message"),
    )
    # 4xx from Plaid is usually our misconfiguration or the user's re-auth,
    # not a client error the browser caused, so report it as a bad gateway.
    return JSONResponse(
        status_code=502,
        content={
            "detail": body.get("error_message") or "Plaid request failed",
            "error_code": error_code,
            "error_type": body.get("error_type"),
            "documentation_url": body.get("documentation_url"),
        },
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(link.router)
app.include_router(sync.router)
app.include_router(transactions.router)
app.include_router(sankey.router)
app.include_router(accounts.router)
app.include_router(bills.router)
app.include_router(oauth.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "plaid_env": settings.plaid_env}
