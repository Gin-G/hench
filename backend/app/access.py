"""Cloudflare Access verification at the origin.

Access authenticates at Cloudflare's edge, then stamps every request it lets
through with a ``Cf-Access-Jwt-Assertion`` header, signed by the team's keys.
Trusting the edge alone is not enough: anything that reaches Traefik without
passing through the tunnel (a leftover router port-forward, a pod inside the
cluster) would carry no Access login at all. So the API checks the signature,
the audience and the email itself, and a request without a valid token never
reaches a router.

The email allow-list duplicates the Access policy on purpose. If that policy
is ever loosened to "any Google account", bank data still stays locked to the
listed addresses.
"""
from __future__ import annotations

import asyncio
import logging

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse

from .config import Settings

log = logging.getLogger("hench.access")

# Kubelet probes hit the pod directly and never carry an Access token. The
# response is only {"status", "plaid_env"}, so leaving it open leaks nothing.
_OPEN_PATHS = frozenset({"/health"})


class AccessVerifier:
    def __init__(self, settings: Settings) -> None:
        self.issuer = f"https://{settings.cf_access_team_domain}"
        self.audience = settings.cf_access_aud
        self.allowed = {e.lower() for e in settings.cf_access_allowed_emails}
        # PyJWKClient caches the key set and refetches on an unknown kid, so
        # Cloudflare's periodic key rotation is picked up without a restart.
        self.jwks = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs")

    def verify(self, token: str) -> str:
        """Return the authenticated email, or raise jwt.PyJWTError."""
        key = self.jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
        email = str(claims.get("email", "")).lower()
        if email not in self.allowed:
            raise jwt.InvalidTokenError(f"{email or 'no email'} is not allowed")
        return email


def install(app, settings: Settings) -> None:
    verifier = AccessVerifier(settings)

    @app.middleware("http")
    async def require_access(request: Request, call_next):
        if request.url.path in _OPEN_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        token = request.headers.get("cf-access-jwt-assertion")
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        try:
            # The key fetch is blocking urllib; keep it off the event loop.
            email = await asyncio.to_thread(verifier.verify, token)
        except jwt.PyJWTError as exc:
            log.warning("rejected access token on %s: %s", request.url.path, exc)
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        request.state.user_email = email
        return await call_next(request)
