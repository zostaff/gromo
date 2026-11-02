"""STEP-2 proof: the sources answer for real. Usage: python scripts/probe.py [graduated_mint]

1 getSlot on desk.rpc_url, 2 one DexScreener tokens/v1 call, 3 one RugCheck report on a graduated
pumpfun mint, 4 getProgramAccounts (Token + Token-2022, memcmp offset 0 = mint) on desk.rpc_url,
5 GROKHOOD launches. Read-only, no keys.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from desk import config, db, fields as F  # noqa: E402
from desk.sources import dexscreener, grokhood, rugcheck, solana_rpc  # noqa: E402


async def pick_mint() -> str | None:
    """A graduated pumpfun mint: a PumpSwap pair from DexScreener search."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.dexscreener.com/latest/dex/search", params={"q": "pumpswap"})
        for p in (r.json() or {}).get("pairs") or []:
            a = F.pair_base_address(p) or ""
            if F.dex_chain(p) == "solana" and F.dex_dex_id(p) == "pumpswap" and a.endswith("pump"):
                return a
    return None


async def main() -> None:
    db.use(":memory:")
    config.load({})
    print(f"rpc: {solana_rpc.url()}")
    t = time.monotonic()
    print(f"1 getSlot: {await solana_rpc.get_slot()} ({1000 * (time.monotonic() - t):.0f} ms)")

    mint = sys.argv[1] if len(sys.argv) > 1 else await pick_mint()
    print(f"graduated mint: {mint}")
    t = time.monotonic()
    pairs = await dexscreener.tokens("solana", [mint])
    best = dexscreener.best_pair(pairs, prefer_dex="pumpswap")
    print(f"2 DexScreener: {len(pairs)} pairs ({1000 * (time.monotonic() - t):.0f} ms), best {F.dex_dex_id(best or {})} "
          f"pool {F.dex_pair_address(best or {})} liq ${F.dex_liquidity(best or {}) or 0:,.0f} "
          f"h1 {F.dex_price_change(best or {}, 'h1')}% metrics {F.pair_metrics(best or {})}")

    t = time.monotonic()
    rep = await rugcheck.report(mint) or {}
    m = F.rc_market_for_pool(rep, F.dex_pair_address(best or {}))
    print(f"3 RugCheck report ({1000 * (time.monotonic() - t):.0f} ms): creator {F.rc_creator(rep)} "
          f"mintAuthority {F.rc_mint_authority(rep)} freezeAuthority {F.rc_freeze_authority(rep)} "
          f"holders {F.rc_total_holders(rep)} markets {len(F.rc_markets(rep))} "
          f"pool LP locked {F.rc_market_lp_locked_pct(m) if m else 'n/a'}%")
    g = await rugcheck.insiders_graph(mint)
    print(f"  insiders graph: {len(F.rcg_networks(g))} networks")

    t = time.monotonic()
    try:
        accs = await solana_rpc.mint_token_accounts(mint)
        holders = sum(1 for a in accs if (F.ta_amount(a) or 0) > 0)
        deleg = sum(1 for a in accs if F.ta_delegate(a))
        print(f"4 getProgramAccounts: {len(accs)} token accounts ({holders} with balance, {deleg} with a delegate) "
              f"in {1000 * (time.monotonic() - t):.0f} ms. Layer 8 works on this RPC.")
    except Exception as e:  # noqa: BLE001
        print(f"4 getProgramAccounts REFUSED: {e}\n  WARNING: auditor layer 8 (delegations) will be red until "
              f"desk.rpc_url points to an RPC that allows getProgramAccounts, e.g. Helius.")
    try:
        t10 = await solana_rpc.top10(mint, F.dex_pair_address(best or {}))
        print(f"  top 10 (pools/curve excluded): {t10['pct']}% excluded owners {t10['excluded']}")
    except Exception as e:  # noqa: BLE001
        print(f"  top 10 failed: {e}")

    page = await grokhood.launches(limit=5) or {}
    items = F.ghl_items(page)
    print(f"5 GROKHOOD launches: {len(items)} items, newest {F.gh_symbol(items[0]) if items else 'n/a'} "
          f"score {F.gh_score(items[0]) if items else 'n/a'}")


if __name__ == "__main__":
    asyncio.run(main())
