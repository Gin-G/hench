"""Read-only Gmail over Google OAuth, with nothing beyond the standard library.

The grant is ``gmail.readonly`` only: the stored refresh token can read mail
and cannot send, change, or delete any. It is obtained once through the
browser (routers.oauth) and kept Fernet-encrypted in ``oauth_tokens``.

Calls are blocking urllib, so the async wrappers push them onto a thread.
"""
from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..crypto import decrypt, encrypt
from ..models import OAuthToken
from .sources import visible_lines

PROVIDER = "google"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


class GoogleAuthError(Exception):
    """The grant is missing, revoked or expired; reconnecting fixes it."""


@dataclass
class Email:
    id: str
    subject: str
    sender: str
    received: datetime
    lines: list[str]


def configured() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def auth_url(state: str) -> str:
    s = get_settings()
    return _AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": s.google_client_id,
            "redirect_uri": s.google_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            # offline + consent: always hand back a refresh token, even when
            # this account has granted the app before.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )


def _post(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
        if body.get("error") in ("invalid_grant", "unauthorized_client"):
            raise GoogleAuthError(
                "Gmail access was revoked or expired — reconnect Gmail"
            ) from e
        raise RuntimeError(
            f"Google {e.code}: {body.get('error_description') or body.get('error')}"
        ) from e


def _get(path: str, access_token: str, params: dict | None = None) -> dict:
    url = f"{_GMAIL}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise GoogleAuthError(f"Gmail refused the request ({e.code})") from e
        raise


def _exchange_sync(code: str) -> tuple[str, str, str]:
    s = get_settings()
    tokens = _post(
        _TOKEN_URL,
        {
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "redirect_uri": s.google_redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if SCOPE not in tokens.get("scope", "").split():
        raise GoogleAuthError("read-only Gmail access was not granted")
    if not tokens.get("refresh_token"):
        raise GoogleAuthError("Google did not return a refresh token")
    email = _get("/profile", tokens["access_token"]).get("emailAddress")
    return tokens["refresh_token"], tokens["scope"], email


async def connect(session: AsyncSession, code: str) -> str:
    """Trade an authorization code for a stored grant. Returns the address."""
    refresh, scope, email = await asyncio.to_thread(_exchange_sync, code)
    row = await session.get(OAuthToken, PROVIDER)
    if row is None:
        row = OAuthToken(provider=PROVIDER)
        session.add(row)
    row.refresh_token, row.scope, row.account_email = encrypt(refresh), scope, email
    await session.flush()
    return email


async def disconnect(session: AsyncSession) -> None:
    row = await session.get(OAuthToken, PROVIDER)
    if row is None:
        return
    try:
        await asyncio.to_thread(_post, _REVOKE_URL, {"token": decrypt(row.refresh_token)})
    except Exception:  # noqa: BLE001 - forget it locally whatever Google says
        pass
    await session.delete(row)


def _access_token_sync(refresh_token: str) -> str:
    s = get_settings()
    return _post(
        _TOKEN_URL,
        {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )["access_token"]


def _text_parts(payload: dict) -> tuple[str | None, str | None]:
    """(text/plain, text/html) bodies, found anywhere in the MIME tree."""
    plain = html = None
    stack = [payload]
    while stack:
        part = stack.pop()
        stack.extend(part.get("parts") or [])
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", "replace"
        )
        if part.get("mimeType") == "text/plain" and plain is None:
            plain = text
        elif part.get("mimeType") == "text/html" and html is None:
            html = text
    return plain, html


def _search_sync(refresh_token: str, query: str, limit: int) -> list[Email]:
    token = _access_token_sync(refresh_token)
    listed = _get("/messages", token, {"q": query, "maxResults": limit})
    emails = []
    for ref in listed.get("messages", []):
        msg = _get(f"/messages/{ref['id']}", token, {"format": "full"})
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        plain, html = _text_parts(msg["payload"])
        # The HTML part is what the sender designed and tends to keep label and
        # value together; plain text is the fallback.
        lines = visible_lines(html) if html else [
            line.strip() for line in (plain or "").splitlines() if line.strip()
        ]
        emails.append(
            Email(
                id=msg["id"],
                subject=headers.get("subject", ""),
                sender=headers.get("from", ""),
                received=datetime.fromtimestamp(
                    int(msg["internalDate"]) / 1000, tz=timezone.utc
                ),
                lines=lines,
            )
        )
    return sorted(emails, key=lambda e: e.received, reverse=True)


async def search(session: AsyncSession, query: str, limit: int = 5) -> list[Email]:
    """Newest-first messages matching a Gmail search query."""
    row = await session.get(OAuthToken, PROVIDER)
    if row is None:
        raise GoogleAuthError("Gmail is not connected")
    return await asyncio.to_thread(_search_sync, decrypt(row.refresh_token), query, limit)
