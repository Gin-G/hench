"""Pydantic request/response models for the frontend API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


# --- Link ------------------------------------------------------------------
class CreateLinkTokenRequest(BaseModel):
    # When set, a link token for *update mode* (re-auth) is created for the
    # given Item instead of a brand-new link.
    item_id: str | None = None


class CreateLinkTokenResponse(BaseModel):
    link_token: str
    expiration: str | None = None


class ExchangePublicTokenRequest(BaseModel):
    public_token: str


class ExchangePublicTokenResponse(BaseModel):
    item_id: str
    institution_name: str | None = None


# --- Items -----------------------------------------------------------------
class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: str
    institution_name: str | None
    status: str
    last_synced_at: datetime | None


# --- Sync ------------------------------------------------------------------
class SyncResult(BaseModel):
    item_id: str
    added: int = 0
    modified: int = 0
    removed: int = 0


# --- Transactions ----------------------------------------------------------
class TransactionOut(BaseModel):
    transaction_id: str
    account_id: str
    date: date
    amount: float
    name: str | None
    merchant_name: str | None
    pending: bool
    # Effective category (override > rule/PFC).
    category_primary: str | None
    category_detailed: str | None
    # Original Plaid category, surfaced so the UI can show what was overridden.
    pfc_primary: str | None
    pfc_detailed: str | None
    is_overridden: bool


class TransactionList(BaseModel):
    total: int
    transactions: list[TransactionOut]


class OverrideRequest(BaseModel):
    category_primary: str
    category_detailed: str | None = None


# --- Sankey ----------------------------------------------------------------
class SankeyLink(BaseModel):
    source: str
    target: str
    value: float


class SankeyNode(BaseModel):
    name: str


class SankeyResponse(BaseModel):
    month: str  # "YYYY-MM"
    nodes: list[SankeyNode]
    links: list[SankeyLink]
    total_income: float
    total_spending: float
