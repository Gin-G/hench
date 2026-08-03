"""Application configuration, driven entirely by environment variables.

Plaid environment switching (sandbox/production) is just the ``plaid_env``
setting — no code paths branch on it beyond picking the API host.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database -----------------------------------------------------------
    # In-cluster this comes from the CNPG `<cluster>-app` secret `uri` key,
    # which is a `postgresql://` URL; db.py rewrites the scheme to asyncpg.
    database_url: str = Field(
        default="postgresql://hench:hench@localhost:5432/hench",
        alias="DATABASE_URL",
    )

    # --- Plaid --------------------------------------------------------------
    plaid_client_id: str = Field(default="", alias="PLAID_CLIENT_ID")
    plaid_secret: str = Field(default="", alias="PLAID_SECRET")
    # one of: sandbox | production
    plaid_env: str = Field(default="sandbox", alias="PLAID_ENV")
    plaid_products: list[str] = Field(
        default_factory=lambda: ["transactions"], alias="PLAID_PRODUCTS"
    )
    # Products consented to at Link time but not initialised with the Item.
    # Plaid only bills these once the endpoint is actually called, and unlike
    # `products` they do not filter out institutions that lack support. The
    # products an Item was linked with are fixed, so anything that might be
    # wanted later has to be listed here *before* the first link — otherwise
    # every institution needs re-authenticating through Link update mode.
    plaid_additional_consented_products: list[str] = Field(
        default_factory=lambda: ["liabilities"],
        alias="PLAID_ADDITIONAL_CONSENTED_PRODUCTS",
    )
    plaid_country_codes: list[str] = Field(
        default_factory=lambda: ["US"], alias="PLAID_COUNTRY_CODES"
    )
    # Optional; required if you register an OAuth redirect URI in the dashboard.
    plaid_redirect_uri: str | None = Field(default=None, alias="PLAID_REDIRECT_URI")
    # Public URL Plaid POSTs webhooks to, e.g. https://hench.nickknows.net/api/webhook
    plaid_webhook_url: str | None = Field(default=None, alias="PLAID_WEBHOOK_URL")

    # --- Crypto -------------------------------------------------------------
    # 32-byte url-safe base64 key. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = Field(default="", alias="FERNET_KEY")

    # --- Server -------------------------------------------------------------
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    log_level: str = Field(default="info", alias="LOG_LEVEL")
    # JSON list, e.g. ["https://hench.nickknows.net"]. Defaults to "*" for dev.
    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"], alias="CORS_ORIGINS"
    )

    @property
    def link_name(self) -> str:
        return "hench"


@lru_cache
def get_settings() -> Settings:
    return Settings()
