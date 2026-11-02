"""Raw-key mapping for every external source. The ONLY module that knows raw JSON keys.

Every key below was read off a real sample saved in logs/raw/ (see docs/FIELDS.md for the
table field -> raw key -> sample file). A key that is not there returns None. Never guess.

Sources: PumpPortal migration message, DexScreener /tokens/v1/{chain}/{addrs} pair,
RugCheck /v1/tokens/{mint}/report and /insiders/graph, Solana RPC jsonParsed answers,
GROKHOOD /v1/launches, /v1/tokens/:a, /holders, /map, /alerts, /chart, SSE /v1/stream.
"""
from __future__ import annotations

from typing import Any


# ---- helpers -----------------------------------------------------------------
def _get(d: Any, *path: Any) -> Any:
    """Walk dict keys / list indexes. Missing anywhere = None."""
    cur = d
    for p in path:
        if isinstance(p, int):
            if not isinstance(cur, list) or not -len(cur) <= p < len(cur):
                return None
            cur = cur[p]
        else:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
    return cur


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


# ---- PumpPortal (logs/raw/pumpportal_migration.json) ------------------------------
# Real message: {"signature","mint","txType":"migrate","pool":"pump-amm"}.
# There is NO creator and NO pool address in the migration message: creator comes from RugCheck,
# the pool address from DexScreener pairAddress / RugCheck markets[].pubkey.
def pp_mint(msg: dict) -> str | None:
    return _get(msg, "mint")


def pp_signature(msg: dict) -> str | None:
    return _get(msg, "signature")


def pp_tx_type(msg: dict) -> str | None:
    return _get(msg, "txType")


def pp_pool_kind(msg: dict) -> str | None:
    """"pump-amm" in the sample: the AMM name, not an address."""
    return _get(msg, "pool")


def pp_creator(msg: dict) -> str | None:
    """Not present in the verified sample. Kept so callers have one place to ask; always None today."""
    return None


def pp_is_migration(msg: dict) -> bool:
    return pp_tx_type(msg) == "migrate" and bool(pp_mint(msg))


# ---- DexScreener pair (logs/raw/dexscreener_tokens_solana.json, dexscreener_tokens_robinhood.json)
def dex_chain(pair: dict) -> str | None:
    return _get(pair, "chainId")


def dex_dex_id(pair: dict) -> str | None:
    return _get(pair, "dexId")


def dex_pair_address(pair: dict) -> str | None:
    return _get(pair, "pairAddress")


def pair_base_address(pair: dict) -> str | None:
    return _get(pair, "baseToken", "address")


dex_base_address = pair_base_address


def dex_base_name(pair: dict) -> str | None:
    return _get(pair, "baseToken", "name")


def dex_base_symbol(pair: dict) -> str | None:
    return _get(pair, "baseToken", "symbol")


def dex_price_usd(pair: dict) -> float | None:
    return _f(_get(pair, "priceUsd"))


def dex_liquidity(pair: dict) -> float | None:
    """liquidity.usd. Absent on a pair a few seconds old (seen in the solana sample)."""
    return _f(_get(pair, "liquidity", "usd"))


def dex_volume(pair: dict, window: str) -> float | None:
    return _f(_get(pair, "volume", window))


def dex_txns(pair: dict, window: str, side: str) -> int | None:
    """side = "buys" | "sells"."""
    return _i(_get(pair, "txns", window, side))


def dex_price_change(pair: dict, window: str) -> float | None:
    return _f(_get(pair, "priceChange", window))


def dex_fdv(pair: dict) -> float | None:
    return _f(_get(pair, "fdv"))


def dex_market_cap(pair: dict) -> float | None:
    return _f(_get(pair, "marketCap"))


def dex_pair_created_ms(pair: dict) -> int | None:
    return _i(_get(pair, "pairCreatedAt"))


def dex_image(pair: dict) -> str | None:
    return _get(pair, "info", "imageUrl")


def pair_metrics(pair: dict) -> dict[str, Any]:
    """Plain numbers for desk/market.py. None when a key is missing."""
    return {
        "priceUsd": dex_price_usd(pair),
        "liquidityUsd": dex_liquidity(pair),
        "marketCap": dex_market_cap(pair),
        "vol_m5": dex_volume(pair, "m5"),
        "vol_h1": dex_volume(pair, "h1"),
        "buys_m5": dex_txns(pair, "m5", "buys"),
        "sells_m5": dex_txns(pair, "m5", "sells"),
        "buys_h1": dex_txns(pair, "h1", "buys"),
        "sells_h1": dex_txns(pair, "h1", "sells"),
        "chg_m5": dex_price_change(pair, "m5"),
        "chg_h1": dex_price_change(pair, "h1"),
    }


# ---- RugCheck report (logs/raw/rugcheck_report.json, rugcheck_report_old.json) ----------
def rc_creator(rep: dict) -> str | None:
    return _get(rep, "creator")


def rc_creator_balance(rep: dict) -> float | None:
    return _f(_get(rep, "creatorBalance"))


def rc_token_program(rep: dict) -> str | None:
    return _get(rep, "tokenProgram")


def rc_mint_authority(rep: dict) -> Any:
    """token.mintAuthority: null when renounced. Use rc_has_key to tell null from missing."""
    return _get(rep, "token", "mintAuthority")


def rc_freeze_authority(rep: dict) -> Any:
    return _get(rep, "token", "freezeAuthority")


def rc_supply(rep: dict) -> int | None:
    return _i(_get(rep, "token", "supply"))


def rc_decimals(rep: dict) -> int | None:
    return _i(_get(rep, "token", "decimals"))


def rc_name(rep: dict) -> str | None:
    return _get(rep, "tokenMeta", "name")


def rc_symbol(rep: dict) -> str | None:
    return _get(rep, "tokenMeta", "symbol")


def rc_total_holders(rep: dict) -> int | None:
    return _i(_get(rep, "totalHolders"))


def rc_top_holders(rep: dict) -> list[dict]:
    return _list(_get(rep, "topHolders"))


def rc_holder_address(h: dict) -> str | None:
    return _get(h, "address")


def rc_holder_owner(h: dict) -> str | None:
    return _get(h, "owner")


def rc_holder_pct(h: dict) -> float | None:
    return _f(_get(h, "pct"))


def rc_markets(rep: dict) -> list[dict]:
    return _list(_get(rep, "markets"))


def rc_market_pubkey(m: dict) -> str | None:
    return _get(m, "pubkey")


def rc_market_type(m: dict) -> str | None:
    return _get(m, "marketType")


def rc_market_lp_locked_pct(m: dict) -> float | None:
    return _f(_get(m, "lp", "lpLockedPct"))


def rc_market_lp_locked_usd(m: dict) -> float | None:
    return _f(_get(m, "lp", "lpLockedUSD"))


def rc_market_liquidity_usd(m: dict) -> float | None:
    """lp.baseUSD + lp.quoteUSD, both must be there."""
    b, q = _f(_get(m, "lp", "baseUSD")), _f(_get(m, "lp", "quoteUSD"))
    return None if b is None or q is None else b + q


def rc_market_for_pool(rep: dict, pool: str | None) -> dict | None:
    if not pool:
        return None
    for m in rc_markets(rep):
        if rc_market_pubkey(m) == pool:
            return m
    return None


def rc_total_market_liquidity(rep: dict) -> float | None:
    return _f(_get(rep, "totalMarketLiquidity"))


def rc_known_accounts(rep: dict) -> dict:
    v = _get(rep, "knownAccounts")
    return v if isinstance(v, dict) else {}


def rc_has_key(rep: dict, *path: str) -> bool:
    cur = rep
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    return True


# ---- RugCheck insiders graph (logs/raw/rugcheck_insiders_graph.json) -----------------------
# A list of networks: {"net_id","network_type","nodes":[{"id","participant","holdings"}],
# "links":[{"source","target"}],"relatedMint"}. An empty list [] = no insider network found.
def rcg_networks(graph: Any) -> list[dict]:
    return graph if isinstance(graph, list) else []


def rcg_nodes(net: dict) -> list[dict]:
    return _list(_get(net, "nodes"))


def rcg_links(net: dict) -> list[dict]:
    return _list(_get(net, "links"))


def rcg_node_id(n: dict) -> str | None:
    return _get(n, "id")


def rcg_node_participant(n: dict) -> bool | None:
    v = _get(n, "participant")
    return v if isinstance(v, bool) else None


def rcg_node_holdings(n: dict) -> int | None:
    return _i(_get(n, "holdings"))


def rcg_link_source(l: dict) -> str | None:
    return _get(l, "source")


def rcg_link_target(l: dict) -> str | None:
    return _get(l, "target")


# ---- Solana RPC (logs/raw/rpc_*.json) ----------------------------------------------------
def rpc_result(resp: dict) -> Any:
    return _get(resp, "result")


def rpc_error(resp: dict) -> Any:
    return _get(resp, "error")


def rpc_value(resp: dict) -> Any:
    """result.value (getAccountInfo, getMultipleAccounts, getTokenAccountsByOwner)."""
    return _get(resp, "result", "value")


def acct_program_owner(acct: dict | None) -> str | None:
    """account.owner = the program that owns the account (rpc_getMultipleAccounts_owner.json)."""
    return _get(acct, "owner")


def acct_parsed_info(acct: dict | None) -> dict | None:
    v = _get(acct, "data", "parsed", "info")
    return v if isinstance(v, dict) else None


def acct_parsed_type(acct: dict | None) -> str | None:
    return _get(acct, "data", "parsed", "type")


# mint account (getAccountInfo jsonParsed, rpc_getAccountInfo_mint.json)
def mint_authority(info: dict) -> Any:
    return _get(info, "mintAuthority")


def freeze_authority(info: dict) -> Any:
    return _get(info, "freezeAuthority")


def mint_has(info: dict, key: str) -> bool:
    return isinstance(info, dict) and key in info


def mint_supply(info: dict) -> int | None:
    return _i(_get(info, "supply"))


def mint_decimals(info: dict) -> int | None:
    return _i(_get(info, "decimals"))


# token account (getProgramAccounts / getTokenAccountsByOwner / getMultipleAccounts jsonParsed)
def ta_owner(info: dict) -> str | None:
    return _get(info, "owner")


def ta_mint(info: dict) -> str | None:
    return _get(info, "mint")


def ta_amount(info: dict) -> int | None:
    """Raw integer amount (tokenAmount.amount is a string)."""
    return _i(_get(info, "tokenAmount", "amount"))


def ta_decimals(info: dict) -> int | None:
    return _i(_get(info, "tokenAmount", "decimals"))


def ta_delegate(info: dict) -> str | None:
    """Key only present when a delegate is set (rpc_getProgramAccounts_delegated.json)."""
    return _get(info, "delegate")


def ta_delegated_amount(info: dict) -> int | None:
    return _i(_get(info, "delegatedAmount", "amount"))


# getProgramAccounts / getTokenAccountsByOwner item: {"pubkey","account"}
def gpa_pubkey(item: dict) -> str | None:
    return _get(item, "pubkey")


def gpa_account(item: dict) -> dict | None:
    return _get(item, "account")


def gpa_info(item: dict) -> dict | None:
    return acct_parsed_info(gpa_account(item))


# getTokenLargestAccounts value item. The public RPC answered 429 on every try
# (logs/raw/rpc_getTokenLargestAccounts_429.json), so these two keys are NOT verified on a
# sample; they follow the Solana RPC docs. Callers fall back to getProgramAccounts (verified).
def tla_address(v: dict) -> str | None:
    return _get(v, "address")


def tla_amount(v: dict) -> int | None:
    return _i(_get(v, "amount"))


# ---- GROKHOOD (logs/raw/grokhood_*.json) ----------------------------------------------------
def ghl_items(page: dict) -> list[dict]:
    """/v1/launches -> items."""
    return _list(_get(page, "items"))


def ghl_next_cursor(page: dict) -> str | None:
    return _get(page, "nextCursor")


def gh_address(t: dict) -> str | None:
    return _get(t, "address")


def gh_name(t: dict) -> str | None:
    return _get(t, "name")


def gh_symbol(t: dict) -> str | None:
    return _get(t, "symbol")


def gh_image(t: dict) -> str | None:
    return _get(t, "imageUrl")


def gh_deployer(t: dict) -> str | None:
    return _get(t, "deployer")


def gh_launched_at(t: dict) -> str | None:
    return _get(t, "launchedAt")


def gh_graduated_at(t: dict) -> str | None:
    return _get(t, "graduatedAt")


def gh_curve_address(t: dict) -> str | None:
    return _get(t, "curve", "address")


def gh_curve_completed(t: dict) -> bool | None:
    v = _get(t, "curve", "completed")
    return v if isinstance(v, bool) else None


def gh_pool_id(t: dict) -> str | None:
    """graduation.poolId: only on the token card (/v1/tokens/:a), not on launches items."""
    return _get(t, "graduation", "poolId")


def gh_score(t: dict) -> int | None:
    return _i(_get(t, "score", "value"))


def gh_score_level(t: dict) -> str | None:
    return _get(t, "score", "level")


def gh_flags(t: dict) -> list[str]:
    return _list(_get(t, "flags"))


def gh_ring(t: dict) -> Any:
    """null when not part of a ring."""
    return _get(t, "ring")


def gh_known_operator(t: dict) -> Any:
    return _get(t, "knownOperator")


def gh_operator_links(t: dict) -> list:
    return _list(_get(t, "operatorLinks"))


def gh_post_status(t: dict) -> str | None:
    return _get(t, "postLaunch", "status")


def gh_post_level(t: dict) -> str | None:
    return _get(t, "postLaunch", "level")


def gh_dev_sold(t: dict) -> dict | None:
    v = _get(t, "postLaunch", "devSold")
    return v if isinstance(v, dict) else None


def gh_dev_sold_pct(t: dict) -> float | None:
    return _f(_get(t, "postLaunch", "devSold", "soldPct"))


def gh_insider_dump(t: dict) -> dict | None:
    v = _get(t, "postLaunch", "insiderDump")
    return v if isinstance(v, dict) else None


def gh_total_supply(t: dict) -> float | None:
    return _f(_get(t, "totalSupply"))


def gh_exempt_count(t: dict) -> int | None:
    return _i(_get(t, "exemptCount"))


def gh_exempt_wallets(t: dict) -> list[str]:
    return _list(_get(t, "exemptWallets"))


def gh_signals(t: dict) -> list[dict]:
    return _list(_get(t, "signals"))


def gh_signal(t: dict, sid: str) -> dict | None:
    for s in gh_signals(t):
        if _get(s, "id") == sid:
            return s
    return None


def gh_signal_severity(s: dict | None) -> str | None:
    """info | warn | danger."""
    return _get(s, "severity")


def gh_signal_value(s: dict | None) -> Any:
    return _get(s, "value")


def gh_signal_reason(s: dict | None) -> str | None:
    return _get(s, "reason")


def gh_insiders_held_pct(t: dict) -> float | None:
    return _f(_get(t, "insiders", "heldPct"))


# /holders (grokhood_token_holders.json)
def ghh_items(h: dict) -> list[dict]:
    return _list(_get(h, "items"))


def ghh_total(h: dict) -> int | None:
    return _i(_get(h, "totalHolders"))


def ghh_wallet(it: dict) -> str | None:
    return _get(it, "wallet")


def ghh_share(it: dict) -> float | None:
    """Fraction of supply, 0..1, given as a string."""
    return _f(_get(it, "share"))


def ghh_sold(it: dict) -> float | None:
    return _f(_get(it, "sold"))


def ghh_flags(it: dict) -> list[str]:
    return _list(_get(it, "flags"))


# /map (grokhood_token_map.json)
def ghm_top_cluster(m: dict) -> dict | None:
    v = _get(m, "summary", "topCluster")
    return v if isinstance(v, dict) else None


def ghm_cluster_count(m: dict) -> int | None:
    return _i(_get(m, "summary", "clusterCount"))


def ghm_cluster_holders(c: dict | None) -> int | None:
    return _i(_get(c, "holderCount"))


def ghm_cluster_wallets(c: dict | None) -> int | None:
    return _i(_get(c, "walletCount"))


def ghm_cluster_share(c: dict | None) -> float | None:
    return _f(_get(c, "share"))


def ghm_total_holders(m: dict) -> int | None:
    return _i(_get(m, "totalHolders"))


def ghm_summary_text(m: dict) -> str | None:
    return _get(m, "summary", "text")


# /alerts (grokhood_token_alerts.json, grokhood_token_alerts_warn.json)
def gha_items(a: dict) -> list[dict]:
    return _list(_get(a, "items"))


def gha_level(it: dict) -> str | None:
    """warn | danger."""
    return _get(it, "level")


def gha_insider_sold_pct(it: dict) -> float | None:
    return _f(_get(it, "insiderSoldPct"))


# /chart (grokhood_token_chart.json): prices in the pair token (ETH/USDG), usdPerQuote may be null
def ghc_last_price(c: dict) -> float | None:
    return _f(_get(c, "last", "price"))


def ghc_usd_per_quote(c: dict) -> float | None:
    return _f(_get(c, "usdPerQuote"))


def ghc_phase(c: dict) -> str | None:
    return _get(c, "phase")


# SSE /v1/stream (grokhood_stream.txt): events launch (Launch item), score {address,score,flags},
# dump {id,address,level,...}, insiders, plus ": ping" comments.
def ghs_address(data: dict) -> str | None:
    return _get(data, "address")


def ghs_score(data: dict) -> int | None:
    return _i(_get(data, "score", "value"))


def ghs_flags(data: dict) -> list[str]:
    return _list(_get(data, "flags"))
