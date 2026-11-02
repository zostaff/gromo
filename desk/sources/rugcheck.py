"""RugCheck client: /v1/tokens/{mint}/report and /v1/tokens/{mint}/insiders/graph (no key needed)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from desk.sources import UA, request_json

log = logging.getLogger("rugcheck")

BASE = "https://api.rugcheck.xyz/v1"
_client: httpx.AsyncClient | None = None


def _c() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30, headers=UA)
    return _client


async def report(mint: str) -> dict | None:
    d = await request_json(_c(), "GET", f"{BASE}/tokens/{mint}/report", name="rugcheck")
    return d if isinstance(d, dict) else None


async def insiders_graph(mint: str) -> list[Any] | None:
    """List of networks; [] = none found. None = RugCheck does not know the mint."""
    d = await request_json(_c(), "GET", f"{BASE}/tokens/{mint}/insiders/graph", name="rugcheck")
    return d if isinstance(d, list) else None
