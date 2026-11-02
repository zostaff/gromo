"""Fresh market read for analyst, executor and risk, plus a lazy push helper.

One place that turns DexScreener pairs into plain numbers:
{priceUsd, liquidityUsd, marketCap, vol_m5, vol_h1, buys_m5, sells_m5, buys_h1, sells_h1, chg_m5, chg_h1}.
Missing = None. Never invented. Raw keys are read only through desk/fields.py.
If desk.sources.dexscreener or desk.fields is not there, every number is None (callers reject "no data").
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

log = logging.getLogger("market")

KEYS = ("priceUsd", "liquidityUsd", "marketCap", "vol_m5", "vol_h1",
        "buys_m5", "sells_m5", "buys_h1", "sells_h1", "chg_m5", "chg_h1")


def empty() -> dict[str, Any]:
    return {k: None for k in KEYS}


def _mod(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as e:
        log.warning("market: %s not available (%s)", name, e)
        return None


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _call(fields, names: tuple[str, ...], *args) -> Any:
    """First accessor in desk.fields that exists, called with args. None if none exists."""
    for n in names:
        fn = getattr(fields, n, None)
        if callable(fn):
            try:
                return fn(*args)
            except Exception as e:  # accessor bug must not kill the agent
                log.warning("fields.%s failed: %s", n, e)
                return None
    return None


def metrics(pair: dict) -> dict[str, Any]:
    """One DexScreener pair -> plain numbers via desk.fields accessors."""
    fields = _mod("desk.fields")
    out = empty()
    if fields is None or not isinstance(pair, dict):
        return out
    whole = _call(fields, ("pair_metrics", "pair_numbers", "market_numbers"), pair)
    if isinstance(whole, dict):
        return {k: _num(whole.get(k)) for k in KEYS}
    out["priceUsd"] = _num(_call(fields, ("pair_price_usd", "price_usd", "ds_price_usd"), pair))
    out["liquidityUsd"] = _num(_call(fields, ("pair_liquidity_usd", "liquidity_usd", "ds_liquidity_usd"), pair))
    out["marketCap"] = _num(_call(fields, ("pair_market_cap", "market_cap", "ds_market_cap"), pair))
    for w in ("m5", "h1"):
        out[f"vol_{w}"] = _num(_call(fields, ("pair_volume", "volume", "ds_volume"), pair, w))
        out[f"buys_{w}"] = _num(_call(fields, ("pair_buys", "buys", "ds_buys"), pair, w))
        out[f"sells_{w}"] = _num(_call(fields, ("pair_sells", "sells", "ds_sells"), pair, w))
        out[f"chg_{w}"] = _num(_call(fields, ("pair_price_change", "price_change", "ds_price_change"), pair, w))
    return out


def _base_address(pair: dict) -> str | None:
    fields = _mod("desk.fields")
    if fields is None:
        return None
    v = _call(fields, ("pair_base_address", "base_address", "ds_base_address"), pair)
    return str(v) if v else None


async def market_reads(chain: str, mints: list[str]) -> dict[str, dict[str, Any]]:
    """Batched read: {mint: numbers}. The deepest pool per mint wins. Absent mint = all None."""
    out = {m: empty() for m in mints}
    if not mints:
        return out
    ds = _mod("desk.sources.dexscreener")
    fn = getattr(ds, "tokens", None) if ds else None
    if fn is None:
        return out
    try:
        pairs = await fn(chain, list(mints)) or []
    except Exception as e:
        log.warning("dexscreener %s read failed: %s", chain, e)
        return out
    lower = {m.lower(): m for m in mints}
    best: dict[str, dict] = {}
    for p in pairs:
        addr = _base_address(p)
        m = lower.get(addr.lower()) if addr else (mints[0] if len(mints) == 1 else None)
        if m is None:
            continue
        nums = metrics(p)
        cur = best.get(m)
        if cur is None or (nums["liquidityUsd"] or 0) > (cur["liquidityUsd"] or 0):
            best[m] = nums
    out.update(best)
    return out


async def market_read(chain: str, mint: str) -> dict[str, Any]:
    return (await market_reads(chain, [mint]))[mint]


async def push(text: str, buttons: list[tuple[str, str]], mint: str | None, kind: str) -> bool:
    """desk.notify.push, imported lazily. Logs and returns False if the notifier is missing or fails."""
    notify = _mod("desk.notify")
    fn = getattr(notify, "push", None) if notify else None
    if fn is None:
        log.warning("push dropped (no desk.notify.push): %s", text.splitlines()[0] if text else "")
        return False
    try:
        await fn(text, buttons, mint, kind)
        return True
    except Exception as e:
        log.warning("push failed: %s", e)
        return False
