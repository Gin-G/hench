"""CLI entrypoint for the nightly sync CronJob and one-off maintenance.

Usage:
    python -m app.cli sync            # sync every user's items
    python -m app.cli sync --item ID  # sync a single item, as its owner
    python -m app.cli longmont        # refresh the owner's Longmont bill now
    python -m app.cli xcel            # re-read the owner's Xcel bill email now
    python -m app.cli encrypt         # encrypt rows still in plaintext
    python -m app.cli keys rewrap     # move locally wrapped keys into OpenBao
    python -m app.cli users           # list users
    python -m app.cli users set-email ID EMAIL
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import Text, cast, or_, select, update
from sqlalchemy.orm.attributes import flag_modified

from . import keys, tenancy, users
from .crypto import legacy
from .db import SessionLocal
from .models import Encrypted, EncryptedJSON, Item, OAuthToken, Owned, User
from .services.longmont import sync_longmont
from .services.sync import sync_all, sync_item
from .services.xcel import sync_xcel

log = logging.getLogger("hench.cli")


async def _run_sync(item_id: str | None) -> None:
    async with SessionLocal() as session:
        if item_id:
            with tenancy.as_system():
                user_id = await session.scalar(
                    select(Item.user_id).where(Item.item_id == item_id)
                )
                user = await session.get(User, user_id) if user_id else None
            if user is None:
                log.error("unknown item_id=%s", item_id)
                return
            await users.unlock(session, user)
            with tenancy.as_user(user.id):
                results = [await sync_item(session, await session.get(Item, item_id))]
                await session.commit()
        else:
            results = await sync_all(session)
    for r in results:
        log.info(
            "item=%s added=%d modified=%d removed=%d",
            r.item_id,
            r.added,
            r.modified,
            r.removed,
        )


async def _as_owner(work) -> None:
    async with SessionLocal() as session:
        owner = await users.owner(session)
        await users.unlock(session, owner)
        with tenancy.as_user(owner.id):
            log.info("%s", await work(session))
            await session.commit()


def _encrypted_columns() -> list[tuple[type, list[str]]]:
    out = []
    for mapper in Owned.__subclasses__():
        cols = [
            c.key
            for c in mapper.__table__.columns
            if isinstance(c.type, (Encrypted, EncryptedJSON))
        ]
        if cols:
            out.append((mapper, cols))
    return out


async def _run_encrypt() -> None:
    """Encrypt every value still stored in plaintext, under its owner's key.

    Safe to run any number of times: values already encrypted are skipped.
    Plaid and Google tokens from before per-user keys are decrypted from
    FERNET_KEY on the way.
    """
    async with SessionLocal() as session:
        with tenancy.as_system():
            everyone = (await session.scalars(select(User).order_by(User.id))).all()
        for user in everyone:
            await users.unlock(session, user)
            with tenancy.as_user(user.id):
                for model, cols in _encrypted_columns():
                    # cast() reads the stored text, not the decrypted value.
                    stale = [
                        cast(getattr(model, c), Text).not_like(f"{keys.PREFIX}%")
                        for c in cols
                    ]
                    rows = (await session.scalars(select(model).where(or_(*stale)))).all()
                    for row in rows:
                        for c in cols:
                            value = getattr(row, c)
                            if value is None:
                                continue
                            if (model, c) in ((Item, "access_token"), (OAuthToken, "refresh_token")):
                                value = legacy(value)
                            setattr(row, c, value)
                            flag_modified(row, c)
                    await session.commit()
                    if rows:
                        log.info("user=%s %s: encrypted %d rows", user.id, model.__tablename__, len(rows))


async def _run_rewrap() -> None:
    """Re-wrap data keys held under FERNET_KEY with each user's OpenBao key."""
    target = keys.provider()
    if target.name != "openbao":
        raise SystemExit("OPENBAO_ADDR is not set; nothing to rewrap into")
    async with SessionLocal() as session:
        with tenancy.as_system():
            local = (
                await session.scalars(select(User).where(User.key_provider == "local"))
            ).all()
            for user in local:
                dek = await keys.unwrap(user.id, user.wrapped_key, "local")
                wrapped = await asyncio.to_thread(target.wrap, user.id, dek)
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(wrapped_key=wrapped, key_provider=target.name)
                )
                await session.commit()
                log.info("user=%s: key moved into OpenBao", user.id)


async def _run_users(set_email: tuple[int, str] | None) -> None:
    async with SessionLocal() as session:
        with tenancy.as_system():
            if set_email:
                user = await session.get(User, set_email[0])
                if user is None:
                    raise SystemExit(f"no user {set_email[0]}")
                user.email = set_email[1].lower()
                await session.commit()
            for u in (await session.scalars(select(User).order_by(User.id))).all():
                print(f"{u.id}\t{u.email}\t{u.key_provider or 'no key yet'}")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="hench")
    sub = parser.add_subparsers(dest="command", required=True)
    sync_cmd = sub.add_parser("sync", help="run a transactions sync")
    sync_cmd.add_argument("--item", dest="item_id", default=None)
    sub.add_parser("longmont", help="refresh the Longmont utility bill")
    sub.add_parser("xcel", help="re-read the Xcel bill email")
    sub.add_parser("encrypt", help="encrypt rows still stored in plaintext")
    keys_cmd = sub.add_parser("keys", help="manage users' data keys")
    keys_cmd.add_argument("action", choices=["rewrap"])
    users_cmd = sub.add_parser("users", help="list users, or fix one's email")
    users_cmd.add_argument("action", nargs="?", choices=["set-email"])
    users_cmd.add_argument("user_id", nargs="?", type=int)
    users_cmd.add_argument("email", nargs="?")
    args = parser.parse_args()

    if args.command == "sync":
        asyncio.run(_run_sync(args.item_id))
    elif args.command == "longmont":
        # force: an explicit run skips the rate limit, though never retries.
        asyncio.run(_as_owner(lambda s: sync_longmont(s, force=True)))
    elif args.command == "xcel":
        asyncio.run(_as_owner(lambda s: sync_xcel(s, force=True)))
    elif args.command == "encrypt":
        asyncio.run(_run_encrypt())
    elif args.command == "keys":
        asyncio.run(_run_rewrap())
    elif args.command == "users":
        if args.action == "set-email" and not (args.user_id and args.email):
            parser.error("users set-email needs a user id and an email")
        asyncio.run(
            _run_users((args.user_id, args.email) if args.action == "set-email" else None)
        )


if __name__ == "__main__":
    main()
