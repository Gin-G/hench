"""CLI entrypoint for the nightly sync CronJob.

Usage:
    python -m app.cli sync            # sync all items
    python -m app.cli sync --item ID  # sync a single item
    python -m app.cli longmont        # refresh the Longmont utility bill now
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from .db import SessionLocal
from .models import Item
from .services.longmont import sync_longmont
from .services.sync import sync_all, sync_item

log = logging.getLogger("hench.cli")


async def _run_sync(item_id: str | None) -> None:
    async with SessionLocal() as session:
        if item_id:
            item = await session.get(Item, item_id)
            if item is None:
                log.error("unknown item_id=%s", item_id)
                return
            result = await sync_item(session, item)
            results = [result]
        else:
            results = await sync_all(session)
        await session.commit()
    for r in results:
        log.info(
            "item=%s added=%d modified=%d removed=%d",
            r.item_id,
            r.added,
            r.modified,
            r.removed,
        )


async def _run_longmont() -> None:
    async with SessionLocal() as session:
        # force: an explicit run skips the rate limit, though never retries.
        log.info("longmont: %s", await sync_longmont(session, force=True))
        await session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="hench")
    sub = parser.add_subparsers(dest="command", required=True)
    sync_cmd = sub.add_parser("sync", help="run a transactions sync")
    sync_cmd.add_argument("--item", dest="item_id", default=None)
    sub.add_parser("longmont", help="refresh the Longmont utility bill")
    args = parser.parse_args()

    if args.command == "sync":
        asyncio.run(_run_sync(args.item_id))
    elif args.command == "longmont":
        asyncio.run(_run_longmont())


if __name__ == "__main__":
    main()
