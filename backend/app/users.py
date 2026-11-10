"""Who is signed in, and unlocking their key.

A user is created the first time an allowed email signs in, and given a
data key then. Both are committed straight away, on their own, before any
of the user's data is touched: a data key that was used but not saved would
leave everything written with it unreadable.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import keys
from .config import get_settings
from .models import User
from .tenancy import as_system

log = logging.getLogger("hench.users")

_locks: dict[int, asyncio.Lock] = {}


async def get_or_create(session: AsyncSession, email: str) -> User:
    email = email.lower()
    with as_system():
        user = await session.scalar(select(User).where(User.email == email))
        if user is not None:
            return user
        user = User(email=email)
        session.add(user)
        try:
            await session.commit()
            log.info("new user=%s", user.id)
        except IntegrityError:
            # Signed in twice at once: the other request made it.
            await session.rollback()
            user = await session.scalar(select(User).where(User.email == email))
    return user


async def owner(session: AsyncSession) -> User:
    """The user the data from before there were users went to."""
    return await get_or_create(session, get_settings().owner)


async def unlock(session: AsyncSession, user: User) -> None:
    """Make the user's data key usable, creating it on first use."""
    if keys.held(user.id):
        return
    lock = _locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        if keys.held(user.id):
            return
        with as_system():
            if user.wrapped_key is None:
                dek = keys.new_key()
                wrapped, provider = await keys.wrap(user.id, dek)
                done = await session.execute(
                    update(User)
                    .where(User.id == user.id, User.wrapped_key.is_(None))
                    .values(wrapped_key=wrapped, key_provider=provider)
                )
                await session.commit()
                if done.rowcount == 1:
                    keys.remember(user.id, dek)
                    log.info("created data key for user=%s via %s", user.id, provider)
                    return
                # Another process got there first; use the key it saved.
            await session.refresh(user)
            keys.remember(
                user.id, await keys.unwrap(user.id, user.wrapped_key, user.key_provider)
            )


async def for_request(session: AsyncSession, request: Request) -> User:
    email = getattr(request.state, "user_email", None)
    if email is None:
        if get_settings().cf_access_enabled:
            raise HTTPException(status_code=401, detail="Unauthorized")
        # Local dev, with no Access in front: the owner.
        email = get_settings().owner
    user = await get_or_create(session, email)
    await unlock(session, user)
    return user
