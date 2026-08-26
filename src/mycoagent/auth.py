"""Optional shared Bearer token (MYCOAGENT_TOKEN). Unset = open local MVP."""

from __future__ import annotations

import os

from fastapi import HTTPException, Request

TOKEN_ENV = "MYCOAGENT_TOKEN"


def resolve_token(explicit: str | None = None) -> str | None:
    """CLI `--token` wins when passed; otherwise env. Empty/whitespace = open."""
    if explicit is not None:
        value = explicit.strip()
    else:
        value = (os.environ.get(TOKEN_ENV) or "").strip()
    return value or None


def bearer_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def check_bearer(authorization: str | None, expected: str | None) -> None:
    """No-op when expected is unset. Otherwise require Authorization: Bearer <expected>."""
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid authorization")
    got = authorization[len("Bearer ") :].strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="invalid token")


async def enforce_bearer(request: Request, expected: str | None) -> None:
    check_bearer(request.headers.get("Authorization"), expected)
