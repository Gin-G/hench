"""The app-wide Fernet key (FERNET_KEY), from before each user had their own.

Plaid and Google tokens used to be encrypted with it; they are now encrypted
with their owner's key like everything else (keys.py), and ``app.cli
encrypt`` rewrites the old ones. Until it has, ``legacy`` reads them. The key
itself is still what wraps data keys in local dev.
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from .config import get_settings

# Every Fernet token starts with its version byte, 0x80, base64-encoded.
_FERNET_PREFIX = "gAAAAA"


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise RuntimeError("FERNET_KEY is not set; cannot decrypt old tokens")
    return Fernet(key.encode() if isinstance(key, str) else key)


def legacy(token: str) -> str:
    """A token as stored before per-user keys, decrypted; anything else as is."""
    if token.startswith(_FERNET_PREFIX):
        return _fernet().decrypt(token.encode()).decode()
    return token
