"""Replay: what happened to every coin the desk decided on, and what the Risk rules would have done.

For each coin with a final decision (Scanner kill, Auditor or Analyst reject, Executor signal) we pull
1-minute candles from GeckoTerminal for [decision, decision + replay.horizon_h] and simulate a buy at the
decision price under the current Risk rules (stop, trailing stop, timeout) with FOMO fees on both sides.

  refused coin (scanner / auditor / analyst): simulated loss = "dodged", simulated gain = "missed"
  signal coin (executor):                     "win" / "loss"

Nothing is guessed: no pool or no candles = no_data, horizon not reached = pending.
GeckoTerminal free API: about 30 calls per minute, so the loop takes one coin every 2.5 s, in small batches.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from . import config, db, fees, links

log = logging.getLogger("replay")

GT = "https://api.geckoterminal.com/api/v2"
GT_NET = {"solana": "solana"}          # Robinhood Chain: no candle source yet -> no_data
GAP_S = 2.5
BATCH = 20
LOOP_S = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS replay (mint TEXT PRIMARY KEY, chain TEXT, stage TEXT, reason TEXT,
  decided_at REAL, status TEXT, price0 REAL, max_pct REAL, min_pct REAL, end_pct REAL,
  sim_pct REAL, sim_exit TEXT, horizon_h REAL, evaluated_at REAL);
CREATE INDEX IF NOT EXISTS replay_decided ON replay(decided_at);
"""
REFUSED = ("scanner", "auditor", "analyst")


def ensure_schema() -> None:
    db.conn().executescript(SCHEMA)


# ---- decisions ------------------------------------------------------------------------------
def decisions(since: float) -> dict[str, dict]:
    """mint -> latest final decision since `since`: {stage, reason, ts, chain}."""
    out: dict[str, dict] = {}

    def put(mint: str, stage: str, reason: str | None, ts: float) -> None:
        cur = out.get(mint)
        if cur is None or ts >= cur["ts"]:
            out[mint] = {"stage": stage, "reason": reason, "ts": float(ts)}

    for r in db.q("SELECT mint, ts, reason FROM scans WHERE verdict='kill' AND ts>=?", (since,)):
        put(r["mint"], "scanner", r["reason"], r["ts"])
    for r in db.q("SELECT mint, ts, agent, subtitle FROM activity WHERE kind='rejected' "
                  "AND agent IN ('auditor','analyst') AND ts>=?", (since,)):
        reason = (r["subtitle"] or "").split(" · ", 1)[-1] or None
        put(r["mint"], r["agent"], reason, r["ts"])
    for r in db.q("SELECT mint, ts FROM signals WHERE status='sent' AND ts>=?", (since,)):
        put(r["mint"], "executor", "Buy button sent", r["ts"])
    for m, d in out.items():
        c = db.coin(m) or {}
        d["chain"] = c.get("chain") or "solana"
    return out


# ---- simulation (pure, tested) --------------------------------------------------------------
def simulate(candles: list[list[float]], t0: float, horizon_s: float, rules: dict) -> dict | None:
    """candles: [[ts, o, h, l, c, v]] any order. Buy at the first candle at/after t0 (its open).
    rules: stop_pct, trail_start_pct, trail_pct, timeout_h, timeout_min_gain_pct, fee_pct (per side).
    Returns {price0, max_pct, min_pct, end_pct, sim_pct, sim_exit} or None without candles."""
    cs = sorted((c for c in candles if t0 - 60 <= c[0] <= t0 + horizon_s), key=lambda c: c[0])
    if not cs:
        return None
    p0 = float(cs[0][1]) or float(cs[0][4])
    if not p0:
        return None
    stop = p0 * (1 - rules["stop_pct"] / 100)
    trail_at = p0 * (1 + rules["trail_start_pct"] / 100)
    timeout_at = t0 + rules["timeout_h"] * 3600
    peak, trailing, exit_px, how = p0, False, None, None
    hi, lo = p0, p0
    for ts, o, h, l, c, *_ in cs:
        hi, lo = max(hi, h), min(lo, l)
        if exit_px is not None:
            continue
        if l <= stop and not trailing:                     # stop first: the conservative order
            exit_px, how = min(stop, o), "stop"
            continue
        if trailing:                                       # peak from earlier candles only: inside one
            tstop = peak * (1 - rules["trail_pct"] / 100)  # candle the order of high and low is unknown
            if l <= tstop:
                exit_px, how = min(tstop, o), "trail"
                continue
        if h >= trail_at:
            trailing = True
        peak = max(peak, h)
        if ts >= timeout_at and (c / p0 - 1) * 100 < rules["timeout_min_gain_pct"]:
            exit_px, how = c, "timeout"
    end = float(cs[-1][4])
    if exit_px is None:
        exit_px, how = end, "horizon"
    fee = 2 * rules["fee_pct"]
    pct = lambda v: round((v / p0 - 1) * 100, 2)  # noqa: E731
    return {"price0": p0, "max_pct": pct(hi), "min_pct": pct(lo), "end_pct": pct(end),
            "sim_pct": round(pct(exit_px) - fee, 2), "sim_exit": how}


def rules_now() -> dict:
    stack = config.stack()
    ticket = max(float(config.get("executor.min_ticket_usd")),
                 stack * float(config.get("executor.stack_pct")) / 100)
    fee_pct = 100 * fees.side_fee(ticket, float(config.get("desk.fees_ref_discount"))) / ticket if ticket else 0
    return {"stop_pct": float(config.get("risk.stop_pct")), "trail_start_pct": float(config.get("risk.trail_start_pct")),
            "trail_pct": float(config.get("risk.trail_pct")), "timeout_h": float(config.get("risk.timeout_h")),
            "timeout_min_gain_pct": float(config.get("risk.timeout_min_gain_pct")), "fee_pct": round(fee_pct, 3)}


# ---- GeckoTerminal ------------------------------------------------------------------------------
class RateLimited(Exception):
    pass


async def _gt(cl: httpx.AsyncClient, path: str) -> Any:
    r = await cl.get(GT + path, headers={"Accept": "application/json"})
    if r.status_code == 429:
        raise RateLimited()
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


async def find_pool(cl: httpx.AsyncClient, net: str, mint: str) -> str | None:
    d = await _gt(cl, f"/networks/{net}/tokens/{mint}/pools?page=1")
    rows = (d or {}).get("data") or []
    return (rows[0].get("attributes") or {}).get("address") if rows else None


async def fetch_candles(cl: httpx.AsyncClient, net: str, pool: str, mint: str, t0: float, horizon_s: float) -> list:
    limit = min(1000, int(horizon_s // 60) + 5)
    d = await _gt(cl, f"/networks/{net}/pools/{pool}/ohlcv/minute?aggregate=1&limit={limit}"
                      f"&before_timestamp={int(t0 + horizon_s) + 60}&currency=usd&token={mint}")
    lst = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    return [[float(x) for x in row[:6]] for row in lst if isinstance(row, list) and len(row) >= 5]


async def evaluate(cl: httpx.AsyncClient, mint: str, d: dict, horizon_h: float, rules: dict) -> dict:
    row = {"mint": mint, "chain": d["chain"], "stage": d["stage"], "reason": d["reason"],
           "decided_at": d["ts"], "horizon_h": horizon_h, "evaluated_at": db.now(), "status": "no_data"}
    net = GT_NET.get(d["chain"])
    if not net:
        return row
    pool = (db.coin(mint) or {}).get("pool")
    if not pool:
        pool = await find_pool(cl, net, mint)
        await asyncio.sleep(GAP_S)
    if not pool:
        return row
    cs = await fetch_candles(cl, net, pool, mint, d["ts"], horizon_h * 3600)
    sim = simulate(cs, d["ts"], horizon_h * 3600, rules)
    if sim:
        row.update(sim)
        row["status"] = "done"
    return row


def save(row: dict) -> None:
    cols = ",".join(row)
    db.x(f"INSERT OR REPLACE INTO replay ({cols}) VALUES ({','.join('?' * len(row))})", row.values())


async def pass_once(max_n: int = BATCH) -> int:
    ensure_schema()
    horizon_h = float(config.get("replay.horizon_h"))
    now = db.now()
    ds = decisions(now - 8 * 86400)
    done = {r["mint"]: r for r in db.q("SELECT mint, decided_at FROM replay")}
    todo = [(m, d) for m, d in ds.items()
            if d["ts"] + horizon_h * 3600 <= now and (m not in done or abs(done[m]["decided_at"] - d["ts"]) > 1)]
    todo.sort(key=lambda x: -x[1]["ts"])
    rules = rules_now()
    n = 0
    async with httpx.AsyncClient(timeout=20) as cl:
        for m, d in todo[:max_n]:
            try:
                save(await evaluate(cl, m, d, horizon_h, rules))
                n += 1
            except RateLimited:
                log.info("geckoterminal 429, pausing the pass")
                break
            except (httpx.HTTPError, ValueError) as e:
                log.warning("replay %s: %s", m[:6], e)
            await asyncio.sleep(GAP_S)
    if n:
        db.kv_set("replay_updated_at", db.now())
    return n


async def loop() -> None:
    while True:
        try:
            await pass_once()
        except Exception:  # noqa: BLE001
            log.exception("replay pass failed")
        await asyncio.sleep(LOOP_S)


# ---- read side ------------------------------------------------------------------------------------
def outcome(stage: str, sim: float) -> str:
    if stage == "executor":
        return "win" if sim > 0 else "loss"
    return "missed" if sim > 0 else "dodged"


def _avg(v: list[float]) -> float | None:
    return round(sum(v) / len(v), 2) if v else None


def summary(window_h: float) -> dict:
    ensure_schema()
    window_h = max(6.0, min(168.0, float(window_h)))
    horizon_h = float(config.get("replay.horizon_h"))
    now = db.now()
    since = now - window_h * 3600
    ds = decisions(since)
    rows = {r["mint"]: r for r in db.q("SELECT * FROM replay WHERE decided_at>=?", (since,))}
    pending = sum(1 for m, d in ds.items() if m not in rows and d["ts"] + horizon_h * 3600 > now)
    done = [r for r in rows.values() if r["status"] == "done"]
    no_data = sum(1 for r in rows.values() if r["status"] != "done")
    refused = [r for r in done if r["stage"] in REFUSED]
    sig = [r for r in done if r["stage"] == "executor"]
    by_stage = []
    for st in ("scanner", "auditor", "analyst", "executor"):
        rs = [r for r in done if r["stage"] == st]
        by_stage.append({"stage": st, "n": len(rs), "dodged": sum(1 for r in rs if r["sim_pct"] <= 0),
                         "avgPct": _avg([r["sim_pct"] for r in rs])})
    out_rows = []
    for r in sorted(done, key=lambda r: -abs(r["sim_pct"] or 0))[:60]:
        c = db.coin(r["mint"]) or {}
        out_rows.append({"mint": r["mint"], "chain": r["chain"], "symbol": c.get("symbol"), "name": c.get("name"),
                         "image": c.get("image"), "stage": r["stage"], "reason": r["reason"],
                         "decidedAt": r["decided_at"], "maxPct": r["max_pct"], "minPct": r["min_pct"],
                         "endPct": r["end_pct"], "simPct": r["sim_pct"], "simExit": r["sim_exit"],
                         "outcome": outcome(r["stage"], r["sim_pct"]),
                         "coinUrl": links.coin_url(r["mint"], r["chain"] or "solana")})
    return {"windowH": window_h, "horizonH": horizon_h, "pending": pending, "noData": no_data,
            "evaluated": len(done), "updatedAt": db.kv_get("replay_updated_at"),
            "summary": {"refused": len(refused), "dodged": sum(1 for r in refused if r["sim_pct"] <= 0),
                        "missed": sum(1 for r in refused if r["sim_pct"] > 0),
                        "dumped": sum(1 for r in done if r["end_pct"] is not None and r["end_pct"] <= -50),
                        "runners": sum(1 for r in done if r["max_pct"] is not None and r["max_pct"] >= 100),
                        "avgRefusedPct": _avg([r["sim_pct"] for r in refused]),
                        "signals": len(sig), "signalAvgPct": _avg([r["sim_pct"] for r in sig])},
            "byStage": by_stage, "rows": out_rows}
