"""Real swaps on Solana through Jupiter Swap V2 (order -> sign -> execute). Used only when the desk is
LIVE with trade.auto on and a wallet key in env. Jupiter lands the transaction; we read the result back
from the chain (token balance) instead of trusting one number.

Keyless Jupiter allows 0.5 requests/s; JUP_API_KEY (free key from developers.jup.ag/portal) raises it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

from desk import wallet
from desk.sources import solana_rpc

log = logging.getLogger("swap")

BASE = "https://api.jup.ag/swap/v2"
PRICE = "https://lite-api.jup.ag/price/v3"
SOL = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000

_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()
_last_call = 0.0
_sol_px: tuple[float, float] | None = None   # (ts, usd)
_decimals: dict[str, int] = {}


class SwapError(Exception):
    pass


def _c() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        h = {"content-type": "application/json", "user-agent": "gromo"}
        if os.environ.get("JUP_API_KEY"):
            h["x-api-key"] = os.environ["JUP_API_KEY"]
        _client = httpx.AsyncClient(timeout=40, headers=h)
    return _client


async def _pace() -> None:
    """Keep under the keyless rate (one call per 2.1 s)."""
    global _last_call
    gap = 0.0 if os.environ.get("JUP_API_KEY") else 2.1
    wait = _last_call + gap - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call = time.monotonic()


async def sol_usd() -> float:
    global _sol_px
    if _sol_px and time.time() - _sol_px[0] < 30:
        return _sol_px[1]
    r = await _c().get(PRICE, params={"ids": SOL})
    v = float(((r.json() or {}).get(SOL) or {}).get("usdPrice") or 0)
    if v <= 0:
        raise SwapError("no SOL price")
    _sol_px = (time.time(), v)
    return v


async def decimals(mint: str) -> int:
    if mint not in _decimals:
        info = await solana_rpc.mint_info(mint)
        if not info or info.get("decimals") is None:
            raise SwapError("no mint decimals")
        _decimals[mint] = int(info["decimals"])
    return _decimals[mint]


async def sol_balance() -> int:
    """Lamports in the desk wallet."""
    res = await solana_rpc.call("getBalance", [wallet.pubkey()])
    return int((res or {}).get("value") or 0) if isinstance(res, dict) else int(res or 0)


async def token_raw(mint: str) -> int:
    return await solana_rpc.token_balance(wallet.pubkey(), mint)


async def _order_execute(inp: str, out: str, amount: int) -> dict:
    """One swap. -> {"signature", "order"}. Raises SwapError with a short reason."""
    if not wallet.available():
        raise SwapError("no wallet")
    async with _lock:
        await _pace()
        r = await _c().get(f"{BASE}/order", params={"inputMint": inp, "outputMint": out, "amount": str(amount),
                                                    "taker": wallet.pubkey()})
        try:
            o = r.json()
        except ValueError:
            raise SwapError(f"order HTTP {r.status_code}") from None
        if r.status_code >= 400 or not o.get("transaction") or not o.get("requestId"):
            raise SwapError(f"no route: {o.get('error') or o.get('errorMessage') or o.get('errorCode') or r.status_code}")
        signed = wallet.sign(o["transaction"])
        await _pace()
        e = await _c().post(f"{BASE}/execute", json={"signedTransaction": signed, "requestId": o["requestId"]})
        try:
            ex = e.json()
        except ValueError:
            raise SwapError(f"execute HTTP {e.status_code}") from None
    if ex.get("status") != "Success":
        raise SwapError(f"swap failed: {ex.get('error') or ex.get('code') or ex.get('status')}")
    log.info("swap ok %s -> %s in=%s sig=%s", inp[:6], out[:6], amount, ex.get("signature"))
    return {"signature": ex.get("signature"), "order": o, "execute": ex}


async def _settled_balance(mint: str, before: int, rising: bool, tries: int = 8) -> int:
    """Token balance after a swap, read until it moved (RPC can lag a slot or two)."""
    v = before
    for _ in range(tries):
        try:
            v = await token_raw(mint)
        except Exception:  # noqa: BLE001
            pass
        if (v > before) if rising else (v < before):
            return v
        await asyncio.sleep(1.5)
    return v


async def buy(mint: str, usd: float) -> dict:
    """Spend `usd` worth of SOL on `mint`. -> {signature, usd_in, tokens_raw, decimals, price_usd}."""
    px = await sol_usd()
    lamports = int(usd / px * LAMPORTS)
    dec = await decimals(mint)
    before = await token_raw(mint)
    res = await _order_execute(SOL, mint, lamports)
    after = await _settled_balance(mint, before, rising=True)
    got = after - before
    if got <= 0:
        got = int(res["execute"].get("outputAmountResult") or res["order"].get("outAmount") or 0)
    if got <= 0:
        raise SwapError("bought but no tokens seen in the wallet")
    usd_in = lamports / LAMPORTS * px
    return {"signature": res["signature"], "usd_in": round(usd_in, 2), "tokens_raw": got, "decimals": dec,
            "price_usd": usd_in / (got / 10 ** dec)}


async def sell(mint: str, tokens_raw: int) -> dict:
    """Sell tokens for SOL. -> {signature, usd_out, tokens_raw, price_usd}."""
    have = await token_raw(mint)
    amount = min(int(tokens_raw), have) if have else int(tokens_raw)
    if amount <= 0:
        raise SwapError("no tokens in the wallet")
    dec = await decimals(mint)
    res = await _order_execute(mint, SOL, amount)
    lam = int(res["execute"].get("outputAmountResult") or res["order"].get("outAmount") or 0)
    px = await sol_usd()
    usd_out = lam / LAMPORTS * px
    return {"signature": res["signature"], "usd_out": round(usd_out, 2), "tokens_raw": amount,
            "price_usd": usd_out / (amount / 10 ** dec) if amount else 0.0}


async def wallet_usd() -> float:
    """Free SOL in the desk wallet, in USD."""
    return await sol_balance() / LAMPORTS * await sol_usd()
