"""Push notifications through ntfy (https://ntfy.sh), self-hosted.

Each user has their own random topic (users.ntfy_topic). The backend
publishes with a token allowed to write ``hench-*`` topics; a phone reads
its topic anonymously, the topic's random name being what keeps it private.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import urllib.error
import urllib.request

from ..config import get_settings
from ..models import User

log = logging.getLogger("hench.notify")

TOPIC_PREFIX = "hench-"


class NotifyError(RuntimeError):
    pass


def available() -> bool:
    return bool(get_settings().ntfy_url)


def public_server() -> str | None:
    s = get_settings()
    url = s.ntfy_public_url or s.ntfy_url
    return url.rstrip("/") if url else None


def topic_for(user: User) -> str:
    """The user's topic, made the first time it is needed (the caller commits)."""
    if not user.ntfy_topic:
        user.ntfy_topic = TOPIC_PREFIX + secrets.token_urlsafe(18)
    return user.ntfy_topic


def _publish(body: dict) -> None:
    s = get_settings()
    headers = {"Content-Type": "application/json"}
    if s.ntfy_token:
        headers["Authorization"] = f"Bearer {s.ntfy_token}"
    req = urllib.request.Request(
        s.ntfy_url.rstrip("/") + "/",
        data=json.dumps(body).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise NotifyError(f"ntfy refused: {e.code} {detail}") from e
    except OSError as e:
        raise NotifyError(f"ntfy unreachable: {e}") from e


async def send(
    user: User,
    title: str,
    message: str,
    *,
    priority: int = 3,
    tags: list[str] | None = None,
    click: str | None = None,
) -> None:
    """Push one notification to the user's topic. Raises NotifyError."""
    if not available():
        raise NotifyError("ntfy is not configured (NTFY_URL)")
    body = {
        "topic": topic_for(user),
        "title": title,
        "message": message,
        "priority": priority,
        "tags": tags or [],
    }
    if click:
        body["click"] = click
    await asyncio.to_thread(_publish, body)
