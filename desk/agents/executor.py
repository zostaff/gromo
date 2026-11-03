"""Executor: sizes the ticket, re-checks the coin, then fills it.

PAPER fills at the signal price at once. LIVE with trade.auto on and a desk wallet (Solana) buys for real
through Jupiter, inside the hard limits (trade.*). Other LIVE coins (Robinhood Chain, or auto off) get ONE
button push and wait for the human's "Bought" (actions.bought) inside executor.confirm_window_min.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from desk import bus, config, db, links, market

log = logging.getLogger("executor")

_watchers: set[asyncio.Task] = set()


# ---- sizing -----------------------------------------------------------------------------
def money_in_open() -> float:
    return float(sum(p["size_usd"] or 0 for p in db.open_positions()))


def size_ticket(stack: float, free: float, liquidity: float | None) -> tuple[float | None, str | None]:
    """-> (size, None) or (None, skip reason). Exactly the brief:
    size = stack_pct% of stack; min(size, max_pool_pct% of liquidity); under min ticket -> bump if free
    covers it (and below_min == bump) else skip "under $47.50"; size > free -> skip "no free stack"."""
    g = config.get
    mt = float(g("executor.min_ticket_usd"))
    size = stack * float(g("executor.stack_pct")) / 100
    if liquidity is not None:
        size = min(size, liquidity * float(g("executor.max_pool_pct")) / 100)
    size = round(size, 2)
    if size < mt:
        if g("executor.below_min") == "bump" and free >= mt:
            size = mt
        else:
            return None, f"under ${mt:.2f}"
    if size > free:
        return None, "no free stack"
    return round(size, 2), None


def recheck(scan: dict, verdict_market: dict, fresh: dict) -> str | None:
    """Fresh read before the push. -> skip reason or None."""
    g = config.get
    price = fresh.get("priceUsd")
    if price is None:
        return "no price"
    liq0, liq = scan.get("liquidityUsd"), fresh.get("liquidityUsd")
    if liq0 and liq is not None and liq < liq0 * (1 - float(g("executor.recheck_liq_drop_pct")) / 100):
        return "liquidity dropping"
    p0 = (verdict_market or {}).get("priceUsd")
    if p0 and price > p0 * (1 + float(g("executor.recheck_price_up_pct")) / 100):
        return "ran before the push"
    return None


# ---- helpers ----------------------------------------------------------------------------
def _night_start() -> float:
    n = db.current_night()
    return float(n["started_at"]) if n else time.time() - 12 * 3600


def pushed_tonight(mint: str) -> bool:
    return db.q1("SELECT 1 AS x FROM signals WHERE mint=? AND ts>=? AND status NOT IN ('skipped')",
                 (mint, _night_start())) is not None


def skipped_by_user(mint: str) -> bool:
    return db.q1("SELECT 1 AS x FROM skips WHERE mint=? AND until>?", (mint, db.now())) is not None


def card(**kw: Any) -> None:
    c = db.kv_get("executor_card", None) or {"trades": 0, "lastSize": None, "state": "idle",
                                               "progress": 0, "name": None}
    c.update(kw)
    c["stackPct"] = float(config.get("executor.stack_pct"))
    c["trades"] = int(db.q1("SELECT COUNT(*) AS n FROM positions")["n"])
    db.kv_set("executor_card", c)


def _skip(mint: str, name: str, reason: str, mode: str, price: float | None = None) -> dict:
    sid = db.insert("signals", {"mint": mint, "ts": db.now(), "price": price, "size_usd": None,
                                "stack_pct": None, "link": None, "mode": mode, "status": "skipped",
                                "reason": reason})
    db.add_activity("skipped", mint, "executor", f"Skipped {name}", f"Executor · {reason}", "Skipped", "", mode)
    log.info("skip %s: %s", name, reason)
    return {"signal_id": sid, "status": "skipped", "reason": reason}


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def auto_on(chain: str) -> bool:
    from desk import wallet
    return (config.get("desk.mode") == "LIVE" and bool(config.get("trade.auto")) and chain == "solana"
            and wallet.available())


def auto_guard() -> str | None:
    """Hard limits before any real buy. -> skip reason or None."""
    g = config.get
    live = [p for p in db.open_positions() if p["mode"] == "LIVE" and p.get("qty_raw")]
    if len(live) >= int(g("trade.max_open")):
        return "max open trades"
    if db.night_loss_live() <= -float(g("trade.daily_loss_usd")):
        return "night loss limit hit"
    return None


def auto_size(stack: float, free_usd: float, liquidity: float | None) -> tuple[float | None, str | None]:
    """stack_pct% of the stack, capped by max_pool_pct% of liquidity, trade.max_ticket_usd and the free SOL."""
    g = config.get
    size = stack * float(g("executor.stack_pct")) / 100
    if liquidity is not None:
        size = min(size, liquidity * float(g("executor.max_pool_pct")) / 100)
    size = min(size, float(g("trade.max_ticket_usd")), free_usd)
    mt = float(g("trade.min_ticket_usd"))
    if size < mt:
        return None, ("wallet empty" if free_usd < mt else f"under ${mt:.2f}")
    return round(size, 2), None


async def auto_buy(mint: str, name: str, chain: str, fresh: dict, scan: dict, audit: dict, verdict: dict) -> dict:
    """A real buy from the desk wallet. Every failure is a skip with the reason, never a retry loop."""
    from desk import swap
    why = auto_guard()
    if why:
        if why == "night loss limit hit" and db.kv_get("halt_pushed") != (db.current_night() or {}).get("id"):
            db.kv_set("halt_pushed", (db.current_night() or {}).get("id"))
            await market.push(f"Night loss limit hit (${float(config.get('trade.daily_loss_usd')):g}). "
                              "No more buys until the next report. Open trades are still guarded.", [], None, "warn")
        return _skip(mint, name, why, "LIVE", fresh.get("priceUsd"))
    try:
        free = await swap.wallet_usd() - float(config.get("trade.sol_reserve")) * await swap.sol_usd()
    except Exception as e:  # noqa: BLE001
        return _skip(mint, name, f"wallet read failed: {type(e).__name__}", "LIVE", fresh.get("priceUsd"))
    db.kv_set("wallet_usd", round(free + float(config.get("trade.sol_reserve")) * await swap.sol_usd(), 2))
    liq = fresh.get("liquidityUsd") if fresh.get("liquidityUsd") is not None else scan.get("liquidityUsd")
    size, why = auto_size(float(config.stack()), max(0.0, free), liq)
    if size is None:
        return _skip(mint, name, why, "LIVE", fresh.get("priceUsd"))
    stack = float(config.stack())
    pct = round(100 * size / stack, 1) if stack > 0 else 0.0
    url = links.coin_url(mint, chain)
    sid = db.insert("signals", {"mint": mint, "ts": db.now(), "price": float(fresh["priceUsd"]), "size_usd": size,
                                "stack_pct": pct, "link": url, "mode": "LIVE", "status": "sent", "reason": None})
    bus.emit("signal", {"signal_id": sid, "mint": mint, "name": name, "chain": chain, "sizeUsd": size,
                        "stackPct": pct, "price": float(fresh["priceUsd"]), "mode": "LIVE", "coinUrl": url})
    card(state="executing", progress=30, name=name, lastSize=size)
    try:
        res = await swap.buy(mint, size)
    except Exception as e:  # noqa: BLE001 - any failure: no position, say why
        reason = str(e)[:80] if isinstance(e, swap.SwapError) else type(e).__name__
        db.update("signals", sid, {"status": "failed", "reason": reason})
        db.add_activity("skipped", mint, "executor", f"Skipped {name}", f"Executor · buy failed: {reason}",
                        "Skipped", "", "LIVE")
        card(state="idle", progress=0)
        await market.push(f"Buy failed {name}: {reason}", [("Open on FOMO", url)], mint, "warn")
        log.warning("auto buy failed %s: %s", name, reason)
        return {"signal_id": sid, "status": "failed", "reason": reason}
    sig = db.q1("SELECT * FROM signals WHERE id=?", (sid,))
    sig.update({"price": res["price_usd"], "size_usd": res["usd_in"]})
    pid = open_position(sig)
    db.update("positions", pid, {"qty_raw": str(res["tokens_raw"]), "decimals": res["decimals"],
                                 "buy_sig": res["signature"]})
    card(state="filled", progress=100, name=name, lastSize=res["usd_in"])
    layers = audit.get("layers") or []
    shield = f"{sum(1 for l in layers if l.get('pass') is True)}/{len(layers) or '?'}"
    await market.push(f"Bought {name} · {_fmt_money(res['usd_in'])} · {pct:g}% of stack\n"
                      f"Auditor {shield} · Analyst {verdict.get('score')} · stop -{float(config.get('risk.stop_pct')):g}%",
                      [("Transaction", f"https://solscan.io/tx/{res['signature']}"), ("Open on FOMO", url)], mint, "signal")
    return {"signal_id": sid, "status": "bought", "position_id": pid, "size": res["usd_in"]}


def open_position(signal: dict) -> int:
    """Open a position from a signal at the signal price. Used by PAPER fill and actions.bought."""
    stop = float(config.get("risk.stop_pct"))
    entry = float(signal["price"])
    pid = db.insert("positions", {
        "mint": signal["mint"], "opened_at": db.now(), "entry_price": entry, "size_usd": signal["size_usd"],
        "peak_price": entry, "stop_pct": stop, "stop_price": entry * (1 - stop / 100), "trail_active": 0,
        "mode": signal["mode"], "status": "open", "last_price": entry, "no_price_polls": 0})
    db.update("signals", signal["id"], {"status": "bought"})
    c = db.coin(signal["mint"]) or {}
    name = c.get("name") or c.get("symbol") or signal["mint"][:6]
    db.add_activity("bought", signal["mint"], "executor", f"Bought {name}", "Executor",
                    _fmt_money(signal["size_usd"]), f"{signal['stack_pct']:g}% of stack", signal["mode"])
    bus.emit("fill", {"position_id": pid, "mint": signal["mint"], "price": entry,
                      "sizeUsd": signal["size_usd"], "mode": signal["mode"]})
    bus.risk.put_nowait({"position_id": pid})
    return pid


async def _paper_bar(name: str) -> None:
    for pr in (25, 50, 75, 100):
        card(state="executing", progress=pr, name=name)
        await asyncio.sleep(0.3)


async def _expire(signal_id: int, minutes: float) -> None:
    await asyncio.sleep(minutes * 60)
    s = db.q1("SELECT * FROM signals WHERE id=?", (signal_id,))
    if s and s["status"] == "sent":
        db.update("signals", signal_id, {"status": "missed", "reason": "no confirm"})
        log.info("signal %s missed", signal_id)
        card(state="idle", progress=0)


# ---- one verdict -------------------------------------------------------------------------
async def handle(msg: dict, paper_bar: bool = True) -> dict:
    scan, audit, verdict = msg.get("scan") or {}, msg.get("audit") or {}, msg.get("verdict") or {}
    mint = scan.get("mint") or verdict.get("mint")
    chain = scan.get("chain") or "solana"
    name = scan.get("name") or scan.get("symbol") or mint[:6]
    mode = str(config.get("desk.mode"))
    db.upsert_coin({"mint": mint, "chain": chain, "name": scan.get("name"), "symbol": scan.get("symbol"),
                    "image": scan.get("image"), "pool": scan.get("pool")})

    if pushed_tonight(mint):
        log.info("already pushed %s tonight", name)
        return {"status": "dup"}
    if skipped_by_user(mint):
        return _skip(mint, name, "you skipped it", mode)
    if len(db.open_positions()) >= int(config.get("executor.max_open")):
        return _skip(mint, name, "max open", mode)

    fresh = await market.market_read(chain, mint)
    why = recheck(scan, msg.get("market") or {}, fresh)
    if why:
        return _skip(mint, name, why, mode, fresh.get("priceUsd"))
    if auto_on(chain):
        return await auto_buy(mint, name, chain, fresh, scan, audit, verdict)

    stack = float(config.stack())
    free = stack - money_in_open()
    liq = fresh.get("liquidityUsd") if fresh.get("liquidityUsd") is not None else scan.get("liquidityUsd")
    size, why = size_ticket(stack, free, liq)
    if size is None:
        return _skip(mint, name, why, mode, fresh.get("priceUsd"))

    pct = round(100 * size / stack, 1) if stack > 0 else 0.0
    url = links.coin_url(mint, chain)
    price = float(fresh["priceUsd"])
    sid = db.insert("signals", {"mint": mint, "ts": db.now(), "price": price, "size_usd": size,
                                "stack_pct": pct, "link": url, "mode": mode, "status": "sent", "reason": None})
    layers = audit.get("layers") or []
    if not layers:
        from desk.agents import auditor
        n_layers = auditor.layer_count(chain)
    else:
        n_layers = len(layers)
    shield = f"{sum(1 for l in layers if l.get('pass') is True)}/{n_layers}"
    stop, trail = float(config.get("risk.stop_pct")), float(config.get("risk.trail_pct"))
    text = (f"{'PAPER · ' if mode == 'PAPER' else ''}Buy {name} · {_fmt_money(size)} · {pct:g}% of stack\n"
            f"Auditor {shield} · Analyst {verdict.get('score')} · stop -{stop:g}% · trail {trail:g}%")
    buttons = [("Buy on FOMO", url), ("Bought", f"cb:bought:{sid}"), ("Skip", f"cb:skip:{sid}")]
    bus.emit("signal", {"signal_id": sid, "mint": mint, "name": name, "chain": chain, "sizeUsd": size,
                        "stackPct": pct, "price": price, "mode": mode, "coinUrl": url})
    card(state="executing", progress=0, name=name, lastSize=size)
    await market.push(text, buttons, mint, "signal")

    signal = db.q1("SELECT * FROM signals WHERE id=?", (sid,))
    if mode == "PAPER":
        if paper_bar:
            await _paper_bar(name)
        pid = open_position(signal)
        card(state="filled", progress=100, name=name, lastSize=size)
        return {"signal_id": sid, "status": "bought", "position_id": pid, "size": size}
    t = asyncio.create_task(_expire(sid, float(config.get("executor.confirm_window_min"))))
    _watchers.add(t)
    t.add_done_callback(_watchers.discard)
    return {"signal_id": sid, "status": "sent", "size": size}


async def run() -> None:
    log.info("executor up")
    card(state="idle", progress=0)
    while True:
        db.beat("executor")
        if db.is_paused("executor"):
            await asyncio.sleep(2)
            continue
        try:
            msg = await asyncio.wait_for(bus.executor.get(), timeout=10)
        except asyncio.TimeoutError:
            continue
        try:
            await handle(msg)
        except Exception:
            log.exception("executor failed on a message")
