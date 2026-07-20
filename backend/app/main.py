"""FastAPI application entrypoint.

Routes are mounted at root (no ``/api`` prefix): in the cluster the Traefik
``strip-api`` middleware rewrites ``/api/*`` -> ``/*`` before traffic reaches
this service, and the Vite dev proxy does the same locally.

Auth is intentionally network-level only (single-user homelab). The structure
leaves room to add OIDC middleware here later without touching the routers.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import link, sankey, sync, transactions

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())

app = FastAPI(title="hench", version="0.1.0")

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


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "plaid_env": settings.plaid_env}
