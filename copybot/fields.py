"""The ONLY place the copy bot reads raw JSON keys (FOMO app API + GROKHOOD token card).

A key that is not there = None. Never guess a value.

FOMO STATUS: NOT VERIFIED. We had no FOMO_TOKEN when this was written; unauthenticated calls return
401 {"error":"unauthorized"}. The FOMO paths below are CANDIDATES ONLY. On the first authenticated run
fomo_api.py saves one raw response per endpoint to logs/raw/fomo_<endpoint>.json and `verify()` writes
logs/raw/fomo_fields_report.json (which logical field resolved through which path). Then:

TODO (first authenticated run):
  1. open logs/raw/fomo_*.json
  2. for every row in FOMO below, keep only the path that is really there, delete the rest
  3. set FOMO_VERIFIED = True
Until then, anything that does not resolve stays None and the card shows "n/a".

GROKHOOD paths are PROVEN on logs/raw/grokhood_token.json and grokhood_token_biaoqing.json.
"""
from __future__ import annotations

from typing import Any

FOMO_VERIFIED = False

# logical field -> candidate paths (dotted, first present wins).   TODO: verify on logs/raw/fomo_*.json
FOMO: dict[str, tuple[str, ...]] = {
    # GET /v2/users/userHandle/{handle}                      -> logs/raw/fomo_user.json
    "user.root":        ("user", "data", ""),
    "user.id":          ("id", "userId", "privyId"),
    "user.handle":      ("userHandle", "handle", "username"),
    "user.name":        ("displayName", "name"),
    "user.avatar":      ("profilePictureUrl", "avatarUrl", "avatar", "pfp"),
    # GET /v2/users/{id}/swaps                                -> logs/raw/fomo_swaps.json
    "swaps.list":       ("swaps", "data", "items", "results", ""),
    "swap.id":          ("id", "swapId", "txHash", "transactionHash", "signature"),
    "swap.ts":          ("createdAt", "timestamp", "time", "blockTime"),
    "swap.side":        ("side", "type", "direction"),            # explicit buy/sell if FOMO sends it
    "swap.chain_id":    ("chainId", "chain.id", "tokenOut.chainId", "tokenIn.chainId"),
    "swap.in_address":  ("tokenIn.address", "inputToken.address", "fromToken.address", "tokenInAddress"),
    "swap.in_symbol":   ("tokenIn.symbol", "inputToken.symbol", "fromToken.symbol", "tokenInSymbol"),
    "swap.out_address": ("tokenOut.address", "outputToken.address", "toToken.address", "tokenOutAddress"),
    "swap.out_symbol":  ("tokenOut.symbol", "outputToken.symbol", "toToken.symbol", "tokenOutSymbol"),
    "swap.usd":         ("usdValue", "valueUsd", "amountUsd", "volumeUsd"),
    # GET /v2/users/{id}/balances                             -> logs/raw/fomo_balances.json
    "balances.list":    ("balances", "data", "items", ""),
    "balance.usd":      ("usdValue", "valueUsd", "balanceUsd"),
    # GET /v2/leaderboard/{24h|7d|30d}                        -> logs/raw/fomo_leaderboard.json
    "leaders.list":     ("leaderboard", "data", "items", "users", ""),
    "leader.user":      ("user", ""),
    "leader.trades":    ("trades", "tradeCount", "numTrades", "swapCount", "stats.trades"),
    "leader.pnl":       ("pnl", "pnlUsd", "realizedPnl", "profit", "stats.pnl"),
    # POST /proxy/filterTokens ["<address>:<chainId>"]        -> logs/raw/fomo_filterTokens.json
    "tokens.list":      ("tokens", "data", "items", ""),
    "token.address":    ("address", "tokenAddress", "mint"),
    "token.chain_id":   ("chainId", "chain.id"),
    "token.symbol":     ("symbol", "ticker"),
    "token.name":       ("name",),
    "token.image":      ("imageUrl", "image", "logoURI", "logo"),
    "token.price":      ("price", "priceUsd", "usdPrice"),
    "token.mcap":       ("marketCap", "marketCapUsd", "mcap", "fdv"),
    "token.liquidity":  ("liquidity", "liquidityUsd", "liquidity.usd"),
}

# PROVEN on logs/raw/grokhood_token*.json (GET https://api.grokhood.tech/v1/tokens/<addr>)
GROKHOOD: dict[str, tuple[str, ...]] = {
    "score":          ("score.value",),       # int 0-100
    "score_level":    ("score.level",),       # "safe" | "caution" | ...
    "ring":           ("ring",),              # null or ring object
    "operator":       ("knownOperator",),     # null or operator object
    "verdict_level":  ("verdict.level",),     # null | "warn" | "danger"
    "verdict_label":  ("verdict.label",),     # human text, e.g. "Dev sold"
}

_MISSING = object()


def _path(obj: Any, path: str) -> Any:
    if path == "":
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _get(table: dict[str, tuple[str, ...]], obj: Any, field: str) -> Any:
    for p in table[field]:
        v = _path(obj, p)
        if v is not _MISSING and v is not None and (p != "" or field.endswith((".list", ".root", ".user"))):
            return v
    return None


def fomo(obj: Any, field: str) -> Any:
    return _get(FOMO, obj, field)


def fomo_list(obj: Any, field: str) -> list:
    for p in FOMO[field]:
        v = _path(obj, p)
        if isinstance(v, list):
            return v
    return []


def grokhood(obj: Any, field: str) -> Any:
    return _get(GROKHOOD, obj, field)


def num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def verify(samples: dict[str, Any]) -> dict[str, str | None]:
    """samples: {"user": raw, "swaps": raw, ...}. Which path each FOMO field resolves to (None = missing)."""
    first = {
        "user": lambda r: fomo(r, "user.root"),
        "swaps": lambda r: (fomo_list(r, "swaps.list") or [None])[0],
        "balances": lambda r: (fomo_list(r, "balances.list") or [None])[0],
        "leaderboard": lambda r: (fomo_list(r, "leaders.list") or [None])[0],
        "filterTokens": lambda r: (fomo_list(r, "tokens.list") or [None])[0],
    }
    prefix = {"user": "user.", "swaps": "swap.", "balances": "balance.", "leaderboard": "leader.",
              "filterTokens": "token."}
    out: dict[str, str | None] = {}
    for name, raw in samples.items():
        if name not in first:
            continue
        item = first[name](raw)
        for field, paths in FOMO.items():
            if field.startswith(prefix[name]) or field.split(".")[0] in (name, name.rstrip("s") + "s"):
                hit = next((p for p in paths if _path(item if field.startswith(prefix[name]) else raw, p)
                            not in (_MISSING, None)), None)
                out[field] = hit
    return out
