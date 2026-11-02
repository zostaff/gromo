"""Solana JSON-RPC client on config desk.rpc_url (jsonParsed everywhere).

Verified on the public mainnet-beta RPC (2026-11-02): getSlot, getAccountInfo, getMultipleAccounts,
getTokenAccountsByOwner and getProgramAccounts (memcmp offset 0 = mint) answer;
getTokenLargestAccounts answers 429 "Too many requests for a specific RPC call" every time,
so top 10 falls back to getProgramAccounts. If an RPC refuses getProgramAccounts, RpcRefused is
raised and auditor layer 8 goes red until desk.rpc_url points at an RPC that allows it (e.g. Helius).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Any

import httpx

from desk import config
from desk import fields as F
from desk.sources import BACKOFF_429_S, SourceError

log = logging.getLogger("solana_rpc")

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"      # bonding curve PDAs
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # PumpSwap pools (seen in sample)
POOL_PROGRAMS = {PUMP_PROGRAM, PUMP_AMM_PROGRAM}

_ids = itertools.count(1)
_client: httpx.AsyncClient | None = None


class RpcError(SourceError):
    pass


class RpcRefused(RpcError):
    """The RPC refuses this method (rate class, plan, disabled index)."""


def _c() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=60, headers={"content-type": "application/json"})
    return _client


def url() -> str:
    return str(config.get("desk.rpc_url"))


async def call(method: str, params: list | None = None, *, tries: int = 3, wait_429: bool = True) -> Any:
    """result or RpcError/RpcRefused. HTTP 429 or JSON-RPC 429 waits 30 s (if wait_429)."""
    last = ""
    for attempt in range(tries):
        body = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or []}
        try:
            r = await _c().post(url(), json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = type(e).__name__
            await asyncio.sleep(2 ** attempt)
            continue
        try:
            d = r.json()
        except ValueError:
            d = {}
        err = F.rpc_error(d) if isinstance(d, dict) else None
        code = (err or {}).get("code") if isinstance(err, dict) else None
        if r.status_code == 429 or code == 429:
            last = "429"
            if not wait_429 or attempt == tries - 1:
                break
            await asyncio.sleep(BACKOFF_429_S)
            continue
        if r.status_code >= 500:
            last = str(r.status_code)
            await asyncio.sleep(2 ** attempt)
            continue
        if err:
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RpcRefused(f"{method}: {msg}")
        if r.status_code >= 400:
            raise RpcRefused(f"{method}: HTTP {r.status_code}")
        if isinstance(d, dict) and "result" in d:
            return F.rpc_result(d)
        last = "no result"
    if last == "429":
        raise RpcRefused(f"{method}: 429 too many requests")
    raise RpcError(f"{method}: {last}")


async def get_slot() -> int:
    return int(await call("getSlot"))


async def account_info(addr: str) -> dict | None:
    res = await call("getAccountInfo", [addr, {"encoding": "jsonParsed"}])
    return F.rpc_value({"result": res})


async def mint_info(mint: str) -> dict | None:
    """Parsed mint info {mintAuthority, freezeAuthority, supply, decimals, ...}."""
    return F.acct_parsed_info(await account_info(mint))


async def multiple_accounts(addrs: list[str]) -> list[dict | None]:
    out: list[dict | None] = []
    for i in range(0, len(addrs), 100):
        res = await call("getMultipleAccounts", [addrs[i:i + 100], {"encoding": "jsonParsed"}])
        out.extend(F.rpc_value({"result": res}) or [])
    return out


async def token_balance(owner: str, mint: str) -> int:
    """Raw token amount the owner wallet holds of this mint (sum of its token accounts)."""
    res = await call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
    total = 0
    for it in F.rpc_value({"result": res}) or []:
        total += F.ta_amount(F.gpa_info(it) or {}) or 0
    return total


async def mint_token_accounts(mint: str) -> list[dict]:
    """Every token account of the mint on Token AND Token-2022 (memcmp offset 0 = mint).
    Returns parsed infos plus "pubkey". Raises RpcRefused when the RPC will not serve it."""
    out: list[dict] = []
    for prog in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        res = await call("getProgramAccounts", [prog, {"encoding": "jsonParsed",
                                                       "filters": [{"memcmp": {"offset": 0, "bytes": mint}}]}],
                         tries=2)
        if not isinstance(res, list):
            raise RpcRefused(f"getProgramAccounts {prog[:6]}: unexpected answer")
        for it in res:
            info = F.gpa_info(it)
            if info is not None:
                out.append({**info, "pubkey": F.gpa_pubkey(it)})
    return out


async def largest_holders(mint: str) -> list[dict]:
    """[{account, owner, amount}] biggest first. getTokenLargestAccounts (+ owners via getMultipleAccounts);
    if the RPC refuses it, the full getProgramAccounts list."""
    try:
        res = await call("getTokenLargestAccounts", [mint], tries=1, wait_429=False)
        vals = F.rpc_value({"result": res}) or []
        addrs = [F.tla_address(v) for v in vals if F.tla_address(v)]
        accs = await multiple_accounts(addrs)
        out = []
        for a, acc in zip(addrs, accs):
            info = F.acct_parsed_info(acc) or {}
            out.append({"account": a, "owner": F.ta_owner(info), "amount": F.ta_amount(info) or 0})
        if out:
            return out
    except RpcError as e:
        log.info("getTokenLargestAccounts refused (%s), using getProgramAccounts", e)
    accs = await mint_token_accounts(mint)
    rows = [{"account": a.get("pubkey"), "owner": F.ta_owner(a), "amount": F.ta_amount(a) or 0} for a in accs]
    rows.sort(key=lambda r: -r["amount"])
    return rows


async def pool_owners(owners: list[str], pool: str | None = None) -> set[str]:
    """Owners that are pools or bonding curves: = pool address, or an account owned by the
    pump curve / PumpSwap AMM program (checked via getMultipleAccounts)."""
    owners = [o for o in dict.fromkeys(owners) if o]
    out = {o for o in owners if pool and o == pool}
    rest = [o for o in owners if o not in out]
    if rest:
        for o, acc in zip(rest, await multiple_accounts(rest)):
            if F.acct_program_owner(acc) in POOL_PROGRAMS:
                out.add(o)
    return out


def top10_pct(holders: list[dict], supply: int, excluded: set[str]) -> float | None:
    """Share of supply held by the 10 biggest owners, pools and curves excluded."""
    if not supply:
        return None
    by: dict[str, int] = {}
    for h in holders:
        o = h.get("owner")
        if not o or o in excluded:
            continue
        by[o] = by.get(o, 0) + int(h.get("amount") or 0)
    top = sorted(by.values(), reverse=True)[:10]
    return round(100.0 * sum(top) / supply, 2)


async def top10(mint: str, pool: str | None = None) -> dict:
    """{"pct": float|None, "excluded": [owners], "supply": int|None}."""
    info = await mint_info(mint)
    supply = F.mint_supply(info or {})
    holders = await largest_holders(mint)
    head = holders[:30]
    excluded = await pool_owners([h["owner"] for h in head if h.get("owner")], pool)
    return {"pct": top10_pct(head, supply or 0, excluded), "excluded": sorted(excluded), "supply": supply}
