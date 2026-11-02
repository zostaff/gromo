"""Auditor: takes every scanner pass apart, 9 layers (8 when auditor.layer9 is off), stops at the first red.

Solana (RPC + RugCheck + DexScreener):
  1 Mint authority renounced          (RPC getAccountInfo jsonParsed)
  2 Freeze authority renounced        (RPC)
  3 Dev wallet < auditor.max_dev_pct and never sold since migration (RPC balance vs snapshot)
  4 Top 10 < auditor.max_top10_pct, pool + curve accounts excluded (RPC owner check)
  5 LP locked/burned >= auditor.min_lp_locked_pct on the RugCheck market whose pubkey = scanner pool,
    and liquidity not down more than 50% since the scan (DexScreener)
  6 Pool age >= auditor.min_pool_age_min
  7 Bundled: biggest connected component of holders in the RugCheck insiders graph < auditor.max_bundled
  8 Delegations: getProgramAccounts on Token AND Token-2022 (memcmp offset 0 = mint), no delegate on more
    than auditor.max_delegated_accounts holder accounts
  9 Dev history: RugCheck report creatorTokens (the creator's other pump.fun launches). Red when the creator
    launched >= auditor.dev_max_launches_7d coins in the last 7 days, or when at least DEV_DEAD_MIN of its
    coins older than DEV_DEAD_AGE_S are judged and more than auditor.dev_max_dead_pct % of them are dead
    (marketCap < auditor.dev_dead_mcap_usd)
Robinhood (GROKHOOD): 1 Safety Score, 2 Rings, 3 Known operator, 4 Dev wallet, 5 Top 10,
  6 Exempt/snipe wallets, 7 Bundled clusters (/map), 8 Dump alerts,
  9 X activity (/x-activity): red when the level is worse than auditor.max_shill_level
    (organic < mixed < spam); tracked with no posts = ok "No X posts"; not tracked = ok "Not tracked on X" (neutral).
Layer 9 runs only when auditor.layer9 is on.

A layer that cannot get data is retried auditor.retries times, 2 s apart, then goes red "no data: <layer>".
Layers after the first red stay pass=None ("not checked").
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from desk import bus, config, db
from desk import fields as F

log = logging.getLogger("auditor")


def pctr(v: float, limit: float) -> str:
    """Percent for a reject reason: 1 decimal near the limit, so "5.3%" never reads as the "5%" limit."""
    return f"{v:.1f}%" if abs(v - limit) < 1 else f"{v:.0f}%"

SOL_LAYERS = ["Mint authority", "Freeze authority", "Dev wallet", "Top 10 holders", "LP locked",
              "Pool age", "Bundled wallets", "Delegations"]
RH_LAYERS = ["Safety Score", "Rings", "Known operator", "Dev wallet", "Top 10 holders",
             "Exempt/snipe wallets", "Bundled clusters", "Dump alerts"]
SOL_LAYER9 = "Dev history"
RH_LAYER9 = "X activity"
SHILL_RANK = {"organic": 0, "mixed": 1, "spam": 2}
DEV_DEAD_MIN = 3            # judged coins needed before the dead share counts
DEV_DEAD_AGE_S = 86400.0    # a coin younger than a day is not judged dead or alive
RETRY_GAP_S = 2.0
_sleep = asyncio.sleep


class NoData(Exception):
    pass


# ---- sources (module-level so tests can replace them) ---------------------------------
async def src_mint_info(mint: str) -> dict | None:
    from desk.sources import solana_rpc
    return await solana_rpc.mint_info(mint)


async def src_balance(owner: str, mint: str) -> int:
    from desk.sources import solana_rpc
    return await solana_rpc.token_balance(owner, mint)


async def src_top10(mint: str, pool: str | None) -> dict:
    from desk.sources import solana_rpc
    return await solana_rpc.top10(mint, pool)


async def src_token_accounts(mint: str) -> list[dict]:
    from desk.sources import solana_rpc
    return await solana_rpc.mint_token_accounts(mint)


async def src_report(mint: str) -> dict | None:
    from desk.sources import rugcheck
    return await rugcheck.report(mint)


async def src_graph(mint: str) -> list | None:
    from desk.sources import rugcheck
    return await rugcheck.insiders_graph(mint)


async def src_pair(chain: str, mint: str, pool: str | None) -> dict | None:
    from desk.sources import dexscreener
    pairs = (await dexscreener.pairs_by_mint(chain, [mint])).get(mint) or []
    return dexscreener.best_pair(pairs, pool=pool, prefer_dex="pumpswap" if chain == "solana" else None)


async def src_gh(kind: str, addr: str) -> dict | None:
    from desk.sources import grokhood
    fn = {"token": grokhood.token, "holders": grokhood.holders, "map": grokhood.token_map,
          "alerts": grokhood.alerts, "x": grokhood.x_activity}[kind]
    return await fn(addr)


# ---- pure helpers (tested) ------------------------------------------------------------
def biggest_component(graph: list | None) -> int:
    """Holders (participant nodes) in the biggest connected component of the insiders graph.
    Networks are merged: a wallet in two networks joins them."""
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    holders: set[str] = set()
    for net in F.rcg_networks(graph):
        for n in F.rcg_nodes(net):
            nid = F.rcg_node_id(n)
            if nid:
                find(nid)
                if F.rcg_node_participant(n) is not False:
                    holders.add(nid)
        for l in F.rcg_links(net):
            s, t = F.rcg_link_source(l), F.rcg_link_target(l)
            if s and t:
                ra, rb = find(s), find(t)
                if ra != rb:
                    parent[ra] = rb
    sizes: dict[str, int] = {}
    for h in holders:
        r = find(h)
        sizes[r] = sizes.get(r, 0) + 1
    return max(sizes.values(), default=0)


def delegation_groups(accounts: list[dict]) -> dict[str, int]:
    """delegate -> number of holder accounts (amount > 0) it can move."""
    out: dict[str, int] = {}
    for a in accounts:
        d = F.ta_delegate(a)
        if d and (F.ta_amount(a) or 0) > 0:
            out[d] = out.get(d, 0) + 1
    return out


def holder_count(accounts: list[dict]) -> int:
    return len({F.ta_owner(a) for a in accounts if (F.ta_amount(a) or 0) > 0 and F.ta_owner(a)})


def _ts(iso_s: str | None) -> float | None:
    if not iso_s:
        return None
    try:
        return datetime.fromisoformat(iso_s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        # RugCheck sends nanoseconds (2026-11-02T09:15:15.53560051Z): cut to microseconds
        head, _, frac = iso_s.rstrip("Z").partition(".")
        try:
            return datetime.fromisoformat(f"{head}.{(frac + '000000')[:6]}+00:00").timestamp()
        except ValueError:
            return None


def dev_history(tokens: list[dict], now: float) -> dict:
    """Creator's other launches -> {total, last7d, judged, dead}. Dead = marketCap under
    auditor.dev_dead_mcap_usd; only coins at least DEV_DEAD_AGE_S old with a market cap are judged."""
    floor = float(config.get("auditor.dev_dead_mcap_usd"))
    last7d = judged = dead = 0
    for t in tokens:
        ts = _ts(F.rc_ct_created_at(t))
        if ts is not None and now - ts < 7 * 86400:
            last7d += 1
        mc = F.rc_ct_market_cap(t)
        if mc is None or ts is None or now - ts < DEV_DEAD_AGE_S:
            continue
        judged += 1
        dead += mc < floor
    return {"total": len(tokens), "last7d": last7d, "judged": judged, "dead": dead}


def short(a: str | None) -> str:
    return "n/a" if not a else (a if len(a) <= 10 else f"{a[:4]}..{a[-4:]}")


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).isoformat()


def night_start() -> float:
    n = db.current_night()
    return float(n["started_at"]) if n else db.now() - 86400


def rejected_tonight(mint: str) -> bool:
    return db.q1("SELECT 1 AS x FROM activity WHERE mint=? AND kind='rejected' AND agent='auditor' AND ts>=?",
                 (mint, night_start())) is not None


# ---- context: lazy, memoized fetches (a failed fetch is not cached) --------------------
class Ctx:
    def __init__(self, scan: dict) -> None:
        self.scan = scan
        self.mint = scan["mint"]
        self.chain = scan.get("chain") or "solana"
        self.coin = db.coin(self.mint) or {}
        self.pool = scan.get("pool") or self.coin.get("pool")
        self._m: dict[str, Any] = {}
        self.out: dict[str, Any] = {}  # holders, bundled, safetyScore

    async def get(self, key: str, fn: Callable[[], Awaitable[Any]], allow_none: bool = False) -> Any:
        if key in self._m:
            return self._m[key]
        v = await fn()
        if v is None and not allow_none:
            raise NoData(key)
        self._m[key] = v
        return v

    def cached(self, key: str) -> Any:
        return self._m.get(key)

    async def mint_info(self) -> dict:
        return await self.get("mint_info", lambda: src_mint_info(self.mint))

    async def report(self) -> dict:
        return await self.get("report", lambda: src_report(self.mint))

    async def pair(self) -> dict:
        return await self.get("pair", lambda: src_pair(self.chain, self.mint, self.pool))

    async def gh(self, kind: str) -> dict:
        return await self.get(f"gh_{kind}", lambda: src_gh(kind, self.mint))


Check = Callable[[Ctx], Awaitable[tuple[bool, str, str, str | None]]]  # pass, value, source, red reason


# ---- Solana layers -----------------------------------------------------------------------
async def sol_mint(c: Ctx):
    info = await c.mint_info()
    if not F.mint_has(info, "mintAuthority"):
        raise NoData("mintAuthority")
    a = F.mint_authority(info)
    return (a is None, "renounced" if a is None else f"set: {short(a)}", "rpc", "mint authority set")


async def sol_freeze(c: Ctx):
    info = await c.mint_info()
    if not F.mint_has(info, "freezeAuthority"):
        raise NoData("freezeAuthority")
    a = F.freeze_authority(info)
    return (a is None, "renounced" if a is None else f"set: {short(a)}", "rpc", "freeze authority set")


async def sol_dev(c: Ctx):
    creator = c.coin.get("creator")
    src = "rpc"
    if not creator:
        creator = F.rc_creator(await c.report())
        src = "rugcheck"
    if not creator:
        raise NoData("creator")
    supply = F.mint_supply(await c.mint_info())
    if not supply:
        raise NoData("supply")
    bal = await c.get("dev_bal", lambda: src_balance(creator, c.mint))
    pct = 100.0 * float(bal) / supply
    mx = float(config.get("auditor.max_dev_pct"))
    at_mig = c.coin.get("creator_balance_at_migration")
    if pct >= mx:
        return (False, f"dev holds {pct:.1f}%", src, f"dev holds {pctr(pct, mx)}")
    if at_mig is not None and float(bal) < float(at_mig):
        sold = 100.0 * (float(at_mig) - float(bal)) / supply
        return (False, f"dev sold {sold:.2f}% of supply since migration", src, "dev sold since migration")
    tail = "not sold since migration" if at_mig is not None else "sold check n/a (no migration snapshot)"
    return (True, f"dev holds {pct:.1f}%, {tail}", src, None)


async def sol_top10(c: Ctx):
    t = await c.get("top10", lambda: src_top10(c.mint, c.pool))
    pct = (t or {}).get("pct")
    if pct is None:
        raise NoData("top10")
    mx = float(config.get("auditor.max_top10_pct"))
    v = f"top 10 hold {pct:.1f}% (pools and curve excluded)"
    return (pct < mx, v, "rpc", f"top 10 hold {pctr(pct, mx)}")


async def sol_lp(c: Ctx):
    pair = await c.pair()
    pool = c.pool or F.dex_pair_address(pair)
    m = F.rc_market_for_pool(await c.report(), pool)
    if m is None:
        raise NoData("rugcheck market for pool")
    locked = F.rc_market_lp_locked_pct(m)
    if locked is None:
        raise NoData("lpLockedPct")
    mn = float(config.get("auditor.min_lp_locked_pct"))
    if locked < mn:
        return (False, f"LP locked {locked:.1f}%", "rugcheck", f"LP locked only {pctr(locked, mn)}")
    now_liq = F.dex_liquidity(pair)
    base = c.scan.get("liquidityUsd") or c.coin.get("liq_at_scan")
    if now_liq is None:
        raise NoData("liquidity")
    if base and now_liq < 0.5 * float(base):
        down = 100.0 * (1 - now_liq / float(base))
        return (False, f"LP locked {locked:.0f}%, liquidity down {down:.0f}%", "rugcheck",
                f"liquidity down {down:.0f}%")
    return (True, f"LP locked {locked:.0f}%, liquidity ${now_liq:,.0f}", "rugcheck", None)


async def sol_age(c: Ctx):
    created = None
    try:
        created = F.dex_pair_created_ms(await c.pair())
    except NoData:
        pass
    t0 = created / 1000 if created else c.coin.get("migrated_at")
    if not t0:
        raise NoData("pool created")
    age = (db.now() - float(t0)) / 60
    mn = float(config.get("auditor.min_pool_age_min"))
    return (age >= mn, f"pool {age:.0f} min old", "dexscreener" if created else "rpc",
            f"pool only {age:.0f} min old")


async def sol_bundled(c: Ctx):
    g = await c.get("graph", lambda: src_graph(c.mint))
    n = biggest_component(g)
    c.out["bundled"] = n
    mx = int(config.get("auditor.max_bundled"))
    return (n < mx, f"biggest linked group {n} wallets", "rugcheck", f"{n} bundled wallets")


async def sol_delegations(c: Ctx):
    accs = await c.get("accounts", lambda: src_token_accounts(c.mint))
    c.out["holders"] = holder_count(accs)
    groups = delegation_groups(accs)
    mx = int(config.get("auditor.max_delegated_accounts"))
    if not groups:
        return (True, "no delegates on holder accounts", "rpc", None)
    d, n = max(groups.items(), key=lambda kv: kv[1])
    v = f"{len(groups)} delegates, biggest {short(d)} on {n} holder accounts"
    return (n <= mx, v, "rpc", f"one delegate on {n} holder accounts")


async def sol_dev_history(c: Ctx):
    rep = await c.report()
    if not F.rc_creator(rep) or not F.rc_has_key(rep, "creatorTokens"):
        raise NoData("creatorTokens")
    h = dev_history(F.rc_creator_tokens(rep), db.now())
    if h["total"] == 0:
        return (True, "No previous launches", "rugcheck", None)
    mx = int(config.get("auditor.dev_max_launches_7d"))
    if h["last7d"] >= mx:
        return (False, f"{h['last7d']} launches in 7d", "rugcheck", f"dev launched {h['last7d']} coins in 7d")
    total = f"{h['total']}+" if h["total"] >= 50 else str(h["total"])
    v = f"{total} launch{'es' if h['total'] != 1 else ''} · {h['dead']} dead"
    if h["judged"] >= DEV_DEAD_MIN and 100.0 * h["dead"] / h["judged"] > float(config.get("auditor.dev_max_dead_pct")):
        return (False, v, "rugcheck", f"dev: {h['dead']} of {h['judged']} coins dead")
    return (True, v, "rugcheck", None)


SOL_CHECKS: list[Check] = [sol_mint, sol_freeze, sol_dev, sol_top10, sol_lp, sol_age, sol_bundled, sol_delegations]


# ---- Robinhood layers ------------------------------------------------------------------------
async def rh_score(c: Ctx):
    t = await c.gh("token")
    s = F.gh_score(t)
    if s is None:
        raise NoData("score")
    c.out["safetyScore"] = s
    mn = int(config.get("auditor.rh_min_score"))
    return (s >= mn, f"Safety Score {s} ({F.gh_score_level(t) or 'n/a'})", "grokhood", f"Safety Score {s}")


async def rh_rings(c: Ctx):
    t = await c.gh("token")
    if "ring" not in t:
        raise NoData("ring")
    r = F.gh_ring(t)
    if r is None:
        return (True, "not part of a known ring", "grokhood", None)
    rid = r.get("id") if isinstance(r, dict) else r
    return (False, f"ring {rid}", "grokhood", "ring launch")


async def rh_operator(c: Ctx):
    t = await c.gh("token")
    if "knownOperator" not in t:
        raise NoData("knownOperator")
    op = F.gh_known_operator(t)
    if op is None:
        return (True, "no known operator", "grokhood", None)
    oid = op.get("id") if isinstance(op, dict) else op
    return (False, f"known operator {oid}", "grokhood", "known operator")


def _rh_share(h: dict, wallet: str | None) -> float:
    if not wallet:
        return 0.0
    return sum(100.0 * (F.ghh_share(i) or 0) for i in F.ghh_items(h) if (F.ghh_wallet(i) or "").lower() == wallet.lower())


async def rh_dev(c: Ctx):
    t = await c.gh("token")
    h = await c.gh("holders")
    if "postLaunch" not in t:
        raise NoData("postLaunch")
    sold = F.gh_dev_sold_pct(t)
    share = _rh_share(h, F.gh_deployer(t))
    mx = float(config.get("auditor.max_dev_pct"))
    if F.gh_dev_sold(t) is not None:
        return (False, f"dev sold {sold if sold is not None else 'n/a'}% of its buy", "grokhood", "dev sold")
    return (share < mx, f"deployer holds {share:.1f}%, not sold", "grokhood", f"dev holds {pctr(share, mx)}")


async def rh_top10(c: Ctx):
    t = await c.gh("token")
    h = await c.gh("holders")
    items = F.ghh_items(h)
    if not items:
        raise NoData("holders")
    skip = {a.lower() for a in (F.gh_curve_address(t), F.gh_pool_id(t)) if a}
    shares = sorted((100.0 * (F.ghh_share(i) or 0) for i in items if (F.ghh_wallet(i) or "").lower() not in skip),
                    reverse=True)[:10]
    pct = sum(shares)
    c.out["holders"] = F.ghh_total(h)
    mx = float(config.get("auditor.max_top10_pct"))
    return (pct < mx, f"top 10 hold {pct:.1f}%", "grokhood", f"top 10 hold {pctr(pct, mx)}")


async def rh_exempt(c: Ctx):
    t = await c.gh("token")
    sigs = [F.gh_signal(t, s) for s in ("exempt_count", "exempt_supply", "block0_snipers")]
    sigs = [s for s in sigs if s]
    if not sigs:
        raise NoData("signals")
    bad = [s for s in sigs if F.gh_signal_severity(s) in ("warn", "danger")]
    if bad:
        return (False, F.gh_signal_reason(bad[0]) or "exempt/snipe warning", "grokhood",
                f"{(bad[0].get('label') or 'snipe').lower()} {F.gh_signal_severity(bad[0])}")
    n = F.gh_exempt_count(t)
    return (True, f"{n if n is not None else 'n/a'} exempt wallets, no snipe flags", "grokhood", None)


async def rh_clusters(c: Ctx):
    m = await c.gh("map")
    top = F.ghm_top_cluster(m)
    n = F.ghm_cluster_holders(top) or 0
    c.out["bundled"] = n
    mx = int(config.get("auditor.max_bundled"))
    share = F.ghm_cluster_share(top)
    v = f"top cluster {n} holders" + (f", {100 * share:.1f}% of supply" if share is not None else "")
    return (n < mx, v, "grokhood", f"cluster of {n} wallets")


async def rh_dumps(c: Ctx):
    a = await c.gh("alerts")
    bad = [i for i in F.gha_items(a) if F.gha_level(i) in ("warn", "danger")]
    if bad:
        lv = "danger" if any(F.gha_level(i) == "danger" for i in bad) else "warn"
        return (False, f"{len(bad)} insider dump alerts ({lv})", "grokhood", f"insider dump {lv}")
    return (True, "no dump alerts", "grokhood", None)


async def rh_x_activity(c: Ctx):
    x = await c.gh("x")
    lv = F.ghx_level(x)
    if lv == "none":
        if F.ghx_tracked(x) is True and (F.ghx_posts(x) or 0) == 0:
            return (True, "No X posts", "grokhood", None)
        if F.ghx_tracked(x) is False:
            # GROKHOOD only tracks X for a subset of launches (42 of 300 on 2026-11-02); an untracked
            # token is a known state, not a failed read, so it passes with a neutral value.
            return (True, "Not tracked on X", "grokhood", None)
        raise NoData("x activity: not tracked")
    if lv not in SHILL_RANK:
        raise NoData(f"x activity level {lv!r}")
    posts, authors = F.ghx_posts(x) or 0, F.ghx_authors(x) or 0
    if SHILL_RANK[lv] > SHILL_RANK[str(config.get("auditor.max_shill_level"))]:
        label = "Coordinated shill" if lv == "spam" else "Mixed shill"
        return (False, f"{label} · {authors} account{'s' if authors != 1 else ''}", "grokhood",
                f"{label.lower()} on X")
    return (True, f"{lv.capitalize()} · {posts} post{'s' if posts != 1 else ''}", "grokhood", None)


RH_CHECKS: list[Check] = [rh_score, rh_rings, rh_operator, rh_dev, rh_top10, rh_exempt, rh_clusters, rh_dumps]


def plan(chain: str) -> tuple[list[str], list[Check]]:
    """Layer names and checks for a chain, in order. Layer 9 last, only when auditor.layer9 is on."""
    if chain == "robinhood":
        names, checks, n9, c9 = list(RH_LAYERS), list(RH_CHECKS), RH_LAYER9, rh_x_activity
    else:
        names, checks, n9, c9 = list(SOL_LAYERS), list(SOL_CHECKS), SOL_LAYER9, sol_dev_history
    if config.get("auditor.layer9"):
        names.append(n9)
        checks.append(c9)
    return names, checks


def layer_count(chain: str = "solana") -> int:
    return len(plan(chain)[0])


# ---- engine ------------------------------------------------------------------------
async def _run_layer(check: Check, c: Ctx) -> tuple[bool, str, str, str | None]:
    tries = 1 + int(config.get("auditor.retries"))
    last: Exception | None = None
    for i in range(tries):
        try:
            return await check(c)
        except Exception as e:  # noqa: BLE001 - NoData, source errors, refusals
            last = e
            log.info("%s %s try %d: %s", c.mint[:6], check.__name__, i + 1, e)
            if i < tries - 1:
                await _sleep(RETRY_GAP_S)
    raise NoData(str(last))


def _card(mint: str, name: str, layers: list[dict], last: str | None = None) -> None:
    card = db.kv_get("auditor_card", {}) or {}
    card["current"] = {"mint": mint, "name": name, "layers": [{"n": l["n"], "pass": l["pass"]} for l in layers]}
    if last is not None:
        card["last"] = last
    db.kv_set("auditor_card", card)


def _merge_extra(mint: str, add: dict) -> None:
    c = db.coin(mint) or {}
    try:
        e = json.loads(c.get("extra") or "{}")
    except ValueError:
        e = {}
    if not isinstance(e, dict):
        e = {}
    e.update({k: v for k, v in add.items() if v is not None})
    db.upsert_coin({"mint": mint, "extra": json.dumps(e)})


async def audit(scan: dict) -> dict:
    """Run the layers for one scanner message. Stores, emits, returns the auditor JSON."""
    c = Ctx(scan)
    mint, chain = c.mint, c.chain
    name = scan.get("name") or scan.get("symbol") or c.coin.get("name") or mint[:6]
    names, checks = plan(chain)
    layers = [{"n": i + 1, "name": n, "pass": None, "value": "not checked",
               "source": "grokhood" if chain == "robinhood" else "rugcheck" if n == SOL_LAYER9 else "rpc"}
              for i, n in enumerate(names)]
    reason: str | None = None
    ts0 = db.now()
    if not config.get("auditor.enabled"):
        reason = "audit off"
        for l in layers:
            bus.emit("audit_layer", {"mint": mint, "n": l["n"], "name": l["name"], "pass": None, "value": l["value"]})
    else:
        _card(mint, name, layers)
        for l, check in zip(layers, checks):
            t = time.monotonic()
            try:
                ok, value, source, red = await _run_layer(check, c)
            except NoData as e:
                ok, value, source, red = False, f"no data: {l['name'].lower()}", l["source"], f"no data: {l['name'].lower()}"
                log.warning("%s layer %d no data: %s", mint[:6], l["n"], e)
            l.update({"pass": ok, "value": value, "source": source, "ms": int(1000 * (time.monotonic() - t))})
            bus.emit("audit_layer", {"mint": mint, "n": l["n"], "name": l["name"], "pass": ok, "value": value})
            _card(mint, name, layers)
            if not ok:
                reason = red or value
                break
    if not (scan.get("name") or c.coin.get("name")):
        t, rep = c.cached("gh_token"), c.cached("report")
        nm = F.gh_name(t) if t else (F.rc_name(rep) if rep else None)
        sy = F.gh_symbol(t) if t else (F.rc_symbol(rep) if rep else None)
        if nm or sy:
            name = nm or sy
            db.upsert_coin({"mint": mint, "name": nm, "symbol": sy})
    passed = reason is None or reason == "audit off"
    holders = c.out.get("holders")
    if holders is None and chain == "solana" and c.cached("report"):
        holders = F.rc_total_holders(c.cached("report"))
    out = {"agent": "auditor", "mint": mint, "chain": chain, "ts": iso(), "passed": passed,
           "layersChecked": sum(1 for l in layers if l["pass"] is not None),
           "layers": [{k: l[k] for k in ("n", "name", "pass", "value", "source")} for l in layers],
           "holders": holders, "reason": reason, "bundled": c.out.get("bundled"),
           "safetyScore": c.out.get("safetyScore")}
    for l in layers:
        db.insert("audits", {"mint": mint, "ts": ts0, "layer": l["n"], "name": l["name"], "value_text": l["value"],
                             "pass": None if l["pass"] is None else int(bool(l["pass"])),
                             "source": l["source"], "ms": l.get("ms")})
    if holders is not None:
        db.upsert_coin({"mint": mint, "holders": int(holders)})
    _merge_extra(mint, {"bundled": out["bundled"], "safetyScore": out["safetyScore"]})
    db.kv_set(f"audit:{mint}", out)
    last = (f"{out['layersChecked']}/{len(layers)} → Executor" if reason is None else
            "audit off → Analyst" if reason == "audit off" else reason)
    _card(mint, name, layers, last)
    bus.emit("audit", out)
    if not passed:
        db.add_activity("rejected", mint, "auditor", f"Rejected {name}", f"Auditor · {reason}", "Skipped", "",
                        str(config.get("desk.mode")))
    log.info("%s %s %s", "PASS" if passed else "REJECT", name, reason or f"{out['layersChecked']}/{len(layers)}")
    return out


async def handle(scan: dict) -> dict | None:
    mint = scan.get("mint")
    if not mint:
        return None
    if rejected_tonight(mint):
        log.info("skip %s: already rejected tonight", mint[:6])
        return None
    res = await audit(scan)
    if res["passed"]:
        await bus.analyst.put({"scan": scan, "audit": res})
    return res


async def audit_now(mint: str, chain: str = "solana") -> dict:
    """Telegram "buy X": audit one coin on demand. A coin rejected tonight returns its stored audit."""
    if rejected_tonight(mint):
        stored = db.kv_get(f"audit:{mint}")
        if isinstance(stored, dict):
            return stored
    c = db.coin(mint) or {}
    chain = c.get("chain") or chain
    scan = {"agent": "scanner", "chain": chain, "mint": mint, "name": c.get("name") or "",
            "symbol": c.get("symbol") or "", "pool": c.get("pool"), "liquidityUsd": c.get("liq_at_scan"),
            "top10Pct": None, "h1ChangePct": None, "ageMin": None, "verdict": "pass", "reason": None,
            "ts": iso(), "image": c.get("image")}
    if not c:
        db.upsert_coin({"mint": mint, "chain": chain, "first_seen": db.now()})
    return await audit(scan)


async def run() -> None:
    log.info("auditor up")
    while True:
        db.beat("auditor")
        if db.is_paused("auditor"):
            await asyncio.sleep(2)
            continue
        try:
            scan = await asyncio.wait_for(bus.auditor.get(), timeout=10)
        except asyncio.TimeoutError:
            continue
        try:
            await handle(scan)
        except Exception:  # noqa: BLE001
            log.exception("audit of %s crashed", scan.get("mint"))
