"""Thin factory around plaid-python, configured from env."""
from __future__ import annotations

from functools import lru_cache

import plaid
from plaid.api import plaid_api

from .config import get_settings

_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


@lru_cache
def get_plaid_client() -> plaid_api.PlaidApi:
    settings = get_settings()
    host = _HOSTS.get(settings.plaid_env.lower())
    if host is None:
        raise RuntimeError(
            f"Unknown PLAID_ENV={settings.plaid_env!r}; expected sandbox|production"
        )
    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": settings.plaid_client_id,
            "secret": settings.plaid_secret,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)
