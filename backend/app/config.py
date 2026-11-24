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

    # --- City of Longmont utility portal ------------------------------------
    # Login for myutilityaccount.longmontcolorado.gov, from OpenBao at
    # hench/longmont. Both empty means the utility fetch is simply off.
    longmont_username: str = Field(default="", alias="LONGMONT_USERNAME")
    longmont_password: str = Field(default="", alias="LONGMONT_PASSWORD")

    # --- Google (Gmail, read-only) -----------------------------------------
    # OAuth client from OpenBao hench/google. The redirect URI must match one
    # registered on that client exactly. Empty client id = Gmail sources off.
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(
        default="http://localhost:5173/api/oauth/google/callback",
        alias="GOOGLE_REDIRECT_URI",
    )

    # --- Server -------------------------------------------------------------
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    log_level: str = Field(default="info", alias="LOG_LEVEL")
    # JSON list, e.g. ["https://hench.nickknows.net"]. Defaults to "*" for dev.
    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"], alias="CORS_ORIGINS"
    )

    # --- Cloudflare Access ----------------------------------------------------
    # Team domain, e.g. "silent-field-be08.cloudflareaccess.com", and the
    # Application Audience (AUD) tag of the hench Access application. Leave
    # both unset for local dev; production refuses to start without them.
    cf_access_team_domain: str = Field(default="", alias="CF_ACCESS_TEAM_DOMAIN")
    cf_access_aud: str = Field(default="", alias="CF_ACCESS_AUD")
    # JSON list. Checked in addition to the Access policy, not instead of it.
    cf_access_allowed_emails: list[str] = Field(
        default_factory=list, alias="CF_ACCESS_ALLOWED_EMAILS"
    )

    # Who the data from before there were separate users belongs to, and
    # who the Longmont utility login is for. Defaults to the first allowed
    # email above.
    owner_email: str = Field(default="", alias="HENCH_OWNER_EMAIL")

    # --- OpenBao Transit: per-user data keys (see keys.py) ------------------
    # Unset = keys wrapped with FERNET_KEY, which production refuses.
    openbao_addr: str = Field(default="", alias="OPENBAO_ADDR")
    openbao_transit_mount: str = Field(default="transit", alias="OPENBAO_TRANSIT_MOUNT")
    openbao_key_prefix: str = Field(default="hench-user-", alias="OPENBAO_KEY_PREFIX")
    # Either a token, or a Kubernetes auth role logged in to with the pod's
    # service account token.
    openbao_token: str = Field(default="", alias="OPENBAO_TOKEN")
    openbao_k8s_role: str = Field(default="hench", alias="OPENBAO_K8S_ROLE")
    openbao_k8s_mount: str = Field(default="kubernetes", alias="OPENBAO_K8S_MOUNT")
    openbao_k8s_jwt_path: str = Field(
        default="/var/run/secrets/kubernetes.io/serviceaccount/token",
        alias="OPENBAO_K8S_JWT_PATH",
    )
    openbao_cacert: str = Field(default="", alias="OPENBAO_CACERT")

    # --- ntfy (budget push notifications) -----------------------------------
    # Where the backend publishes: in-cluster, the ntfy Service. Empty = off.
    ntfy_url: str = Field(default="", alias="NTFY_URL")
    # What a phone subscribes to: the public address of the same server.
    # Defaults to ntfy_url.
    ntfy_public_url: str = Field(default="", alias="NTFY_PUBLIC_URL")
    # Access token of the ntfy user allowed to publish to hench-* topics.
    ntfy_token: str = Field(default="", alias="NTFY_TOKEN")
    # The app's own address, opened by tapping a notification.
    app_url: str = Field(default="", alias="HENCH_APP_URL")

    @property
    def owner(self) -> str:
        email = self.owner_email or next(iter(self.cf_access_allowed_emails), "")
        return (email or "owner@localhost").lower()

    @property
    def cf_access_enabled(self) -> bool:
        return bool(self.cf_access_team_domain and self.cf_access_aud)

    @property
    def link_name(self) -> str:
        return "hench"


@lru_cache
def get_settings() -> Settings:
    return Settings()
