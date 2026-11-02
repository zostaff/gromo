"""DexScreener client. /tokens/v1/{chain}/{addr,addr,...}: up to 30 addresses per call.

Rate limit: under 250 requests per minute (we pace at 240/min = one call per 0.25 s).
Chain ids that work (verified 2026-11-02): "solana", "robinhood" (Robinhood Chain, Uniswap v4 pools).
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from desk import fields as F
from desk.sources import UA, request_json

log = logging.getLogger("dexscreener")

BASE = "https://api.dexscreener.com"
BATCH = 30
MIN_INTERVAL_S = 60.0 / 240

_lock = asyncio.Lock()
_last = 0.0
_client: httpx.AsyncClient | None = None


def _c() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, headers=UA)
    return _client


async def _pace() -> None:
    global _last
    async with _lock:
        wait = _last + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()


async def tokens(chain: str, mints: list[str]) -> list[dict]:
    """All pairs for these token addresses, flat list (desk/market.py contract). Batches of 30."""
    out: list[dict] = []
    uniq = list(dict.fromkeys(m for m in mints if m))
    for i in range(0, len(uniq), BATCH):
        part = uniq[i:i + BATCH]
        await _pace()
        data = await request_json(_c(), "GET", f"{BASE}/tokens/v1/{chain}/{','.join(part)}",
                                  name="dexscreener")
        if isinstance(data, list):
            out.extend(p for p in data if isinstance(p, dict))
    return out


def by_mint(pairs: list[dict], mints: list[str]) -> dict[str, list[dict]]:
    """Group pairs by base token address (case-insensitive: Robinhood answers checksummed)."""
    low = {m.lower(): m for m in mints}
    out: dict[str, list[dict]] = {m: [] for m in mints}
    for p in pairs:
        a = F.pair_base_address(p)
        m = low.get(a.lower()) if a else None
        if m is not None:
            out[m].append(p)
    return out


def best_pair(pairs: list[dict], pool: str | None = None, prefer_dex: str | None = None) -> dict | None:
    """The pair whose address = pool if given, else the deepest pair (prefer_dex first)."""
    if not pairs:
        return None
    if pool:
        for p in pairs:
            if F.dex_pair_address(p) == pool:
                return p
    cand = [p for p in pairs if prefer_dex and F.dex_dex_id(p) == prefer_dex] or pairs
    return max(cand, key=lambda p: F.dex_liquidity(p) or 0.0)


async def pairs_by_mint(chain: str, mints: list[str]) -> dict[str, list[dict]]:
    return by_mint(await tokens(chain, mints), mints)
