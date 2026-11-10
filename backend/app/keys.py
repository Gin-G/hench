"""Per-user encryption of what identifies someone's money.

Each user has their own data key: random, 32 bytes, never stored in the
clear. What is stored, on the ``users`` row, is that key wrapped (encrypted)
by the user's own key in OpenBao's Transit engine, ``hench-user-<id>``. To
read or write a user's rows the app asks OpenBao to unwrap it, keeps it in
memory for a few minutes, and encrypts and decrypts locally with it — one
OpenBao call per user per few minutes rather than one per value.

A database dump or backup on its own therefore shows balances and dates but
no names: account names and numbers, transaction descriptions, merchants,
bill and holding names and notes, and the Plaid and Google tokens are all
ciphertext. And one user's rows cannot be decrypted with another's key, so
a query that somehow crossed users would fail rather than leak.

What it does not do: keep data from whoever runs the server. The app's own
OpenBao identity can unwrap every user's key, because the nightly sync has to
write a user's data while they are not there.

Without OpenBao configured (local dev), data keys are wrapped with
FERNET_KEY instead. Production refuses to start that way; ``python -m
app.cli keys rewrap`` moves keys wrapped locally into OpenBao.

Values are stored as ``hx1:<user id>:<Fernet token>``. Anything without the
prefix is from before encryption and is read as it is, until ``python -m
app.cli encrypt`` rewrites it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings
from .tenancy import current_user_id

log = logging.getLogger("hench.keys")

PREFIX = "hx1:"
# How long an unwrapped key stays in memory after it was last used.
_TTL = 600.0


class KeyUnavailable(RuntimeError):
    """A user's key is not available: not unlocked, or OpenBao refused."""


# --- Wrapping: OpenBao Transit, or FERNET_KEY for local dev ----------------


class _Local:
    name = "local"

    def _kek(self) -> Fernet:
        key = get_settings().fernet_key
        if not key:
            raise KeyUnavailable("FERNET_KEY is not set; cannot wrap data keys locally")
        return Fernet(key.encode())

    def wrap(self, user_id: int, dek: bytes) -> str:
        return self._kek().encrypt(dek).decode()

    def unwrap(self, user_id: int, wrapped: str) -> bytes:
        return self._kek().decrypt(wrapped.encode())


class _OpenBao:
    name = "openbao"

    def __init__(self) -> None:
        s = get_settings()
        self.addr = s.openbao_addr.rstrip("/")
        self.mount = s.openbao_transit_mount.strip("/")
        self.key_prefix = s.openbao_key_prefix
        self.static_token = s.openbao_token
        self.k8s_role = s.openbao_k8s_role
        self.k8s_mount = s.openbao_k8s_mount.strip("/")
        self.k8s_jwt_path = s.openbao_k8s_jwt_path
        self.ctx = ssl.create_default_context(cafile=s.openbao_cacert or None)
        self._token: str | None = None
        self._token_expires = 0.0
        self._lock = threading.Lock()
        self._created: set[int] = set()

    def key_name(self, user_id: int) -> str:
        return f"{self.key_prefix}{user_id}"

    def _call(self, path: str, body: dict | None, token: str | None) -> dict:
        req = urllib.request.Request(
            f"{self.addr}/v1/{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method="POST" if body is not None else "GET",
            headers={"Content-Type": "application/json"}
            | ({"X-Vault-Token": token} if token else {}),
        )
        with urllib.request.urlopen(req, timeout=10, context=self.ctx) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}

    def _login(self) -> str:
        if self.static_token:
            return self.static_token
        with self._lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token
            with open(self.k8s_jwt_path) as f:
                jwt = f.read().strip()
            auth = self._call(
                f"auth/{self.k8s_mount}/login", {"role": self.k8s_role, "jwt": jwt}, None
            )["auth"]
            self._token = auth["client_token"]
            # Renew well before the lease runs out.
            self._token_expires = time.monotonic() + max(30, auth["lease_duration"] * 0.8)
            return self._token

    def _transit(self, op: str, user_id: int, body: dict | None) -> dict:
        path = f"{self.mount}/{op}/{self.key_name(user_id)}"
        for attempt in (0, 1):
            try:
                return self._call(path, body, self._login())
            except urllib.error.HTTPError as e:
                if e.code == 403 and attempt == 0 and not self.static_token:
                    # Token expired or revoked: log in again once.
                    self._token = None
                    continue
                detail = e.read().decode(errors="replace")[:200]
                raise KeyUnavailable(f"OpenBao {op} for user {user_id}: {e.code} {detail}") from e
        raise AssertionError("unreachable")

    def wrap(self, user_id: int, dek: bytes) -> str:
        if user_id not in self._created:
            # Creating a key that exists already is a no-op in Transit.
            self._transit("keys", user_id, {"type": "aes256-gcm96"})
            self._created.add(user_id)
        out = self._transit(
            "encrypt", user_id, {"plaintext": base64.b64encode(dek).decode()}
        )
        return out["data"]["ciphertext"]

    def unwrap(self, user_id: int, wrapped: str) -> bytes:
        out = self._transit("decrypt", user_id, {"ciphertext": wrapped})
        return base64.b64decode(out["data"]["plaintext"])


_providers: dict[str, object] = {}


def provider(name: str | None = None):
    """The wrapping provider by name; the configured one by default."""
    if name is None:
        name = "openbao" if get_settings().openbao_addr else "local"
    if name not in _providers:
        _providers[name] = _OpenBao() if name == "openbao" else _Local()
    return _providers[name]


# --- Unlocked data keys, in memory -----------------------------------------

_unlocked: dict[int, tuple[Fernet, float]] = {}


def _remember(user_id: int, dek: bytes) -> None:
    _unlocked[user_id] = (Fernet(base64.urlsafe_b64encode(dek)), time.monotonic() + _TTL)


def _key_for(user_id: int) -> Fernet:
    held = _unlocked.get(user_id)
    now = time.monotonic()
    if held is None or held[1] < now:
        raise KeyUnavailable(f"user {user_id}'s key is not unlocked")
    # Each use extends it, so a long sync never loses its key halfway.
    _unlocked[user_id] = (held[0], now + _TTL)
    return held[0]


def held(user_id: int) -> bool:
    entry = _unlocked.get(user_id)
    return entry is not None and entry[1] >= time.monotonic()


def new_key() -> bytes:
    return os.urandom(32)


async def wrap(user_id: int, dek: bytes) -> tuple[str, str]:
    """Wrap a new data key with the configured provider: (wrapped, provider)."""
    p = provider()
    return await asyncio.to_thread(p.wrap, user_id, dek), p.name


async def unwrap(user_id: int, wrapped: str, provider_name: str | None) -> bytes:
    return await asyncio.to_thread(provider(provider_name).unwrap, user_id, wrapped)


def remember(user_id: int, dek: bytes) -> None:
    _remember(user_id, dek)


def forget(user_id: int) -> None:
    _unlocked.pop(user_id, None)


# --- Values ------------------------------------------------------------------


def encrypt(plaintext: str) -> str:
    """Encrypt for the user in scope."""
    user_id = current_user_id()
    token = _key_for(user_id).encrypt(plaintext.encode()).decode()
    return f"{PREFIX}{user_id}:{token}"


def decrypt(stored: str) -> str:
    """Decrypt a stored value; one from before encryption comes back as is."""
    if not stored.startswith(PREFIX):
        return stored
    user_id, _, token = stored[len(PREFIX) :].partition(":")
    try:
        return _key_for(int(user_id)).decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise KeyUnavailable(f"value does not decrypt with user {user_id}'s key") from e


def is_encrypted(stored: str | None) -> bool:
    return stored is not None and stored.startswith(PREFIX)
