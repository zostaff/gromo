"""Scanner: finds fresh pools and decides pass / kill. Pass goes to the Auditor.

Solana: PumpPortal migration -> waiting room. Every scanner.poll_s the room is re-read on DexScreener
(batched 30 per call), prices go to db.prices, then the kill order runs per coin:
  1 liquidity < scanner.min_liquidity_usd   (wait: liquidity can still come in)
  2 top 10 > scanner.max_top10_pct           (kill; pools and curves excluded via RPC owner check)
  3 1h change > scanner.max_h1_change_pct    (kill)
  4 copycat of a runner today (24h >= scanner.runner_h24_pct, normalized names)  (kill)
then pass once the pool is scanner.min_pool_age_min old. A coin still waiting after
scanner.max_wait_min is dropped as "never qualified: <last blocker>". One pass per mint per night.
Creator + creator balance are stored at migration (migration message has no creator: RugCheck).

Robinhood: GROKHOOD stream + launches page. Launches that graduated (Uniswap v4 pool) enter the room,
gate: liquidity >= scanner.rh_min_liquidity_usd (DexScreener chain "robinhood") and pool age.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

from desk import bus, config, db
from desk import fields as F

log = logging.getLogger("scanner")

FEED_LEN = 6
TOP10_TTL_S = 300
RUNNER_TTL_S = 86400

room: dict[str, dict] = {}            # mint -> state while waiting
_top10_cache: dict[str, tuple[float, float | None]] = {}
TOP10_FAIL_TTL_S = 300               # a failed top 10 is not retried on every poll
_poll_n = 0


# ---- small helpers ----------------------------------------------------------------
def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).isoformat()


def parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def night_start() -> float:
    n = db.current_night()
    return float(n["started_at"]) if n else db.now() - 86400


def passed_tonight(mint: str) -> bool:
    return db.q1("SELECT 1 AS x FROM scans WHERE mint=? AND verdict='pass' AND ts>=?",
                 (mint, night_start())) is not None


def decided_tonight(mint: str) -> bool:
    return db.q1("SELECT 1 AS x FROM scans WHERE mint=? AND ts>=?", (mint, night_start())) is not None


def money(v: float | None) -> str:
    return "n/a" if v is None else f"${v:,.0f}"


# ---- copycat ---------------------------------------------------------------------------
_AFFIX = re.compile(r"^(the)|(coin|token|onsol)$")


def normalize_name(s: str | None) -> str:
    """Lowercase ASCII letters+digits only, accents folded, leading "the" and trailing
    coin/token/onsol dropped. "The Pepe Coin!" == "PEPE" == "pépé"."""
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s)
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    t = re.sub(r"[^a-z0-9]", "", t)
    for _ in range(2):
        u = _AFFIX.sub("", t)
        if not u or u == t:
            break
        t = u
    return t


def runners_today(now: float | None = None) -> dict[str, dict]:
    """Coins seen in the last 24 h with 24h change >= scanner.runner_h24_pct."""
    now = now or db.now()
    floor = float(config.get("scanner.runner_h24_pct"))
    return {m: r for m, r in (db.kv_get("scanner_runners", {}) or {}).items()
            if now - float(r.get("ts") or 0) <= RUNNER_TTL_S and (r.get("h24") or 0) >= floor}


def note_runner(mint: str, name: str | None, symbol: str | None, h24: float | None) -> None:
    if h24 is None:
        return
    allr = db.kv_get("scanner_runners", {}) or {}
    now = db.now()
    allr = {m: r for m, r in allr.items() if now - float(r.get("ts") or 0) <= RUNNER_TTL_S}
    allr[mint] = {"name": name, "symbol": symbol, "h24": h24, "ts": now}
    db.kv_set("scanner_runners", allr)


def copycat_of(mint: str, name: str | None, symbol: str | None, runners: dict[str, dict]) -> dict | None:
    keys = {k for k in (normalize_name(name), normalize_name(symbol)) if len(k) >= 2}
    if not keys:
        return None
    for m, r in runners.items():
        if m == mint:
            continue
        rk = {k for k in (normalize_name(r.get("name")), normalize_name(r.get("symbol"))) if len(k) >= 2}
        if keys & rk:
            return {"mint": m, **r}
    return None


# ---- the decision -----------------------------------------------------------------
def evaluate(c: dict, chain: str = "solana") -> tuple[str, str | None]:
    """c: {liq, top10, h1, age_min, waited_min, copycat}. Returns (wait|kill|pass, reason).
    Kill order: liquidity, top 10, 1h change, copycat. Then pool age."""
    max_wait = float(config.get("scanner.max_wait_min"))
    blocker: str | None = None
    if chain == "robinhood":
        min_liq = float(config.get("scanner.rh_min_liquidity_usd"))
    else:
        min_liq = float(config.get("scanner.min_liquidity_usd"))
    liq = c.get("liq")
    if liq is None:
        blocker = "liquidity n/a"
    elif liq < min_liq:
        blocker = f"liquidity {money(liq)} < {money(min_liq)}"
    if blocker is None and chain == "solana":
        t10 = c.get("top10")
        mx = float(config.get("scanner.max_top10_pct"))
        if t10 is None:
            blocker = "top 10 n/a"
        elif t10 > mx:
            return "kill", f"top 10 hold {t10:.1f}% > {mx:g}%"
    if blocker is None:
        h1 = c.get("h1")
        mx = float(config.get("scanner.max_h1_change_pct"))
        if h1 is not None and h1 > mx:
            return "kill", f"1h change +{h1:.0f}% > {mx:g}%"
        cc = c.get("copycat")
        if cc:
            return "kill", f"copycat of {cc.get('name') or cc.get('symbol')} (+{cc.get('h24', 0):.0f}% 24h)"
        age = c.get("age_min")
        min_age = float(config.get("scanner.min_pool_age_min"))
        if age is None:
            blocker = "pool age n/a"
        elif age < min_age:
            blocker = f"pool {age:.0f} min < {min_age:g} min"
        else:
            return "pass", None
    if (c.get("waited_min") or 0) >= max_wait:
        return "kill", f"never qualified: {blocker}"
    return "wait", blocker


# ---- sources (patched in tests) ------------------------------------------------------------
async def dex_pairs(chain: str, mints: list[str]) -> dict[str, list[dict]]:
    from desk.sources import dexscreener
    return await dexscreener.pairs_by_mint(chain, mints)


def best_pair(pairs: list[dict], pool: str | None, chain: str) -> dict | None:
    from desk.sources import dexscreener
    return dexscreener.best_pair(pairs, pool=pool, prefer_dex="pumpswap" if chain == "solana" else None)


async def top10_of(mint: str, pool: str | None) -> float | None:
    from desk.sources import solana_rpc
    return (await solana_rpc.top10(mint, pool))["pct"]


async def creator_snapshot(mint: str) -> tuple[str | None, float | None]:
    """(creator, raw balance at migration). Creator from RugCheck (migration message has none)."""
    from desk.sources import rugcheck, solana_rpc
    rep = await rugcheck.report(mint)
    creator = F.rc_creator(rep or {})
    if not creator:
        return None, None
    try:
        bal = float(await solana_rpc.token_balance(creator, mint))
    except Exception as e:  # noqa: BLE001
        log.warning("creator balance %s: %s", mint[:6], e)
        bal = F.rc_creator_balance(rep or {})
    return creator, bal


# ---- outputs ------------------------------------------------------------------------
def _feed_push(item: dict) -> None:
    feed = [item] + [f for f in (db.kv_get("scanner_feed", []) or []) if f.get("mint") != item["mint"]]
    feed = feed[:FEED_LEN]
    db.kv_set("scanner_feed", feed)
    pools = db.q1("SELECT COUNT(*) AS c FROM coins WHERE first_seen>=?", (night_start(),))
    db.kv_set("scanner_card", {"pools": int(pools["c"]) if pools else 0, "feed": feed})


async def decide(mint: str, st: dict, verdict: str, reason: str | None) -> dict:
    now = db.now()
    chain = st.get("chain", "solana")
    age = st.get("age_min")
    db.insert("scans", {"mint": mint, "ts": now, "liquidity_usd": st.get("liq"), "top10_pct": st.get("top10"),
                        "h1_change_pct": st.get("h1"), "age_min": age, "verdict": verdict, "reason": reason})
    db.upsert_coin({"mint": mint, "chain": chain, "liq_at_scan": st.get("liq"), "pool": st.get("pool"),
                    "name": st.get("name"), "symbol": st.get("symbol"), "image": st.get("image")})
    msg = {"agent": "scanner", "chain": chain, "mint": mint, "name": st.get("name") or "",
           "symbol": st.get("symbol") or "", "pool": st.get("pool"), "liquidityUsd": st.get("liq"),
           "top10Pct": st.get("top10"), "h1ChangePct": st.get("h1"),
           "ageMin": int(age) if age is not None else None, "verdict": verdict, "reason": reason,
           "ts": iso(now), "image": st.get("image")}
    item = {"name": st.get("name"), "symbol": st.get("symbol"), "mint": mint, "chain": chain,
            "verdict": verdict, "reason": reason, "liq": st.get("liq")}
    _feed_push(item)
    bus.emit("scan", msg)
    log.info("%s %s %s %s", verdict.upper(), chain, st.get("symbol") or mint[:6], reason or "")
    if verdict == "pass":
        await bus.auditor.put(msg)
    room.pop(mint, None)
    return msg


# ---- room in ----------------------------------------------------------------------
def enter(mint: str, chain: str, t0: float, **kw: Any) -> bool:
    if mint in room or decided_tonight(mint):
        return False
    now = db.now()
    room[mint] = {"chain": chain, "t0": t0, "entered": now, **kw}
    db.upsert_coin({"mint": mint, "chain": chain, "migrated_at": t0, "first_seen": now,
                    "name": kw.get("name"), "symbol": kw.get("symbol"), "image": kw.get("image"),
                    "pool": kw.get("pool")})
    return True


# Quote tokens sometimes show up as the "mint" of a migration message; they are not coins to scan
# (and a holder scan of USDC kills the RPC).
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


async def on_migration(msg: dict) -> None:
    mint = F.pp_mint(msg)
    if not mint or not config.get("scanner.solana") or mint in QUOTE_MINTS:
        return
    if not enter(mint, "solana", db.now(), sig=F.pp_signature(msg)):
        return
    log.info("migration %s", mint)
    creator = F.pp_creator(msg)
    try:
        c2, bal = await creator_snapshot(mint)
        creator = creator or c2
        db.upsert_coin({"mint": mint, "creator": creator, "creator_balance_at_migration": bal})
    except Exception as e:  # noqa: BLE001
        log.warning("creator snapshot %s failed: %s", mint[:6], e)


# ---- polling --------------------------------------------------------------------------
async def _top10_cached(mint: str, pool: str | None) -> float | None:
    hit = _top10_cache.get(mint)
    if hit and time.time() - hit[0] < (TOP10_TTL_S if hit[1] is not None else TOP10_FAIL_TTL_S):
        return hit[1]
    try:
        v = await top10_of(mint, pool)
    except Exception as e:  # noqa: BLE001
        log.warning("top10 %s: %s", mint[:6], e)
        v = None
    _top10_cache[mint] = (time.time(), v)
    return v


async def poll_chain(chain: str) -> None:
    mints = [m for m, s in room.items() if s["chain"] == chain]
    if not mints:
        return
    pairs = await dex_pairs(chain, mints)
    runners = runners_today()
    now = db.now()
    for mint in mints:
        st = room.get(mint)
        if st is None:
            continue
        if passed_tonight(mint):
            room.pop(mint, None)
            continue
        p = best_pair(pairs.get(mint) or [], st.get("pool"), chain)
        if p is not None:
            st["pool"] = st.get("pool") or F.dex_pair_address(p)
            st["name"] = st.get("name") or F.dex_base_name(p)
            st["symbol"] = st.get("symbol") or F.dex_base_symbol(p)
            st["image"] = st.get("image") or F.dex_image(p)
            st["liq"] = F.dex_liquidity(p)
            st["h1"] = F.dex_price_change(p, "h1")
            created = F.dex_pair_created_ms(p)
            pool_t0 = created / 1000 if created else st["t0"]
            db.add_price(mint, F.dex_price_usd(p), F.dex_volume(p, "m5"))
            note_runner(mint, st.get("name"), st.get("symbol"), F.dex_price_change(p, "h24"))
        else:
            pool_t0 = st["t0"]
        st["age_min"] = round(max(0.0, now - pool_t0) / 60, 1)
        waited = (now - st["entered"]) / 60
        st["copycat"] = copycat_of(mint, st.get("name"), st.get("symbol"), runners)
        liq_ok = st.get("liq") is not None and st["liq"] >= float(
            config.get("scanner.min_liquidity_usd" if chain == "solana" else "scanner.rh_min_liquidity_usd"))
        if chain == "solana" and liq_ok:
            st["top10"] = await _top10_cached(mint, st.get("pool"))
        verdict, reason = evaluate({**st, "waited_min": waited}, chain)
        st["blocker"] = reason
        if verdict != "wait":
            await decide(mint, st, verdict, reason)


async def refresh_runners() -> None:
    """Re-read coins decided in the last 24 h so today's runners stay current (Solana)."""
    rows = db.q("SELECT DISTINCT s.mint FROM scans s JOIN coins c ON c.mint=s.mint "
                "WHERE s.ts>=? AND c.chain='solana' ORDER BY s.ts DESC LIMIT 300", (db.now() - RUNNER_TTL_S,))
    mints = [r["mint"] for r in rows if r["mint"] not in room]
    if not mints:
        return
    pairs = await dex_pairs("solana", mints)
    for m in mints:
        p = best_pair(pairs.get(m) or [], None, "solana")
        if p is not None:
            note_runner(m, F.dex_base_name(p), F.dex_base_symbol(p), F.dex_price_change(p, "h24"))


# ---- Robinhood -------------------------------------------------------------------------
def rh_enter(item: dict) -> None:
    addr = (F.gh_address(item) or "").lower()
    grad = parse_iso(F.gh_graduated_at(item))
    if not addr or grad is None or not config.get("scanner.robinhood"):
        return
    if db.now() - grad > float(config.get("scanner.max_wait_min")) * 60:
        return  # graduated before we were watching: not a fresh pool
    if enter(addr, "robinhood", grad, name=F.gh_name(item), symbol=F.gh_symbol(item),
             image=F.gh_image(item), pool=F.gh_pool_id(item)):
        log.info("robinhood graduate %s %s", F.gh_symbol(item), addr)


async def rh_poll_launches() -> None:
    from desk.sources import grokhood
    page = await grokhood.launches(limit=100)
    for it in F.ghl_items(page or {}):
        rh_enter(it)


async def rh_stream() -> None:
    from desk.sources import grokhood
    async for ev, data in grokhood.stream({"launch", "score"}):
        if not config.get("scanner.robinhood"):
            continue
        try:
            if ev == "score" and "graduated" in F.ghs_flags(data):
                addr = F.ghs_address(data)
                if addr and addr.lower() not in room:
                    tok = await grokhood.token(addr)
                    if tok:
                        rh_enter(tok)
            elif ev == "launch":
                rh_enter(data)  # only enters if it already graduated
        except Exception as e:  # noqa: BLE001
            log.warning("grokhood event %s: %s", ev, e)


# ---- loops -------------------------------------------------------------------------------
async def solana_listener() -> None:
    from desk.sources import pumpportal
    async for msg in pumpportal.migrations():
        asyncio.create_task(on_migration(msg))


async def poll_once() -> None:
    global _poll_n
    _poll_n += 1
    if config.get("scanner.robinhood"):
        try:
            await rh_poll_launches()
        except Exception as e:  # noqa: BLE001
            log.warning("grokhood launches: %s", e)
    for chain, on in (("solana", config.get("scanner.solana")), ("robinhood", config.get("scanner.robinhood"))):
        if not on:
            continue
        try:
            await poll_chain(chain)
        except Exception as e:  # noqa: BLE001
            log.warning("poll %s: %s", chain, e)
    if _poll_n % 5 == 0 and config.get("scanner.solana"):
        try:
            await refresh_runners()
        except Exception as e:  # noqa: BLE001
            log.warning("runners refresh: %s", e)


def restore_room() -> None:
    """After a restart: coins that entered within max_wait and have no decision go back in."""
    floor = db.now() - float(config.get("scanner.max_wait_min")) * 60
    for r in db.q("SELECT * FROM coins WHERE first_seen>=? AND migrated_at IS NOT NULL", (floor,)):
        if r["mint"] not in room and not decided_tonight(r["mint"]):
            room[r["mint"]] = {"chain": r["chain"] or "solana", "t0": r["migrated_at"], "entered": r["first_seen"],
                               "name": r["name"], "symbol": r["symbol"], "image": r["image"], "pool": r["pool"]}


async def _forever(name: str, fn) -> None:
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("%s crashed, restarting in 5s", name)
        await asyncio.sleep(5)


async def poll_loop() -> None:
    while True:
        db.beat("scanner")
        if not db.is_paused("scanner"):
            await poll_once()
        await asyncio.sleep(float(config.get("scanner.poll_s")))


async def run() -> None:
    restore_room()
    log.info("scanner up, %d coins restored to the waiting room", len(room))
    await asyncio.gather(_forever("pumpportal", solana_listener), _forever("grokhood stream", rh_stream),
                         _forever("poll", poll_loop))
