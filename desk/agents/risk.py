"""Risk: guards open positions. Polls prices, moves the peak, fires stop / trail / timeout / pulled.

Rules in order: peak update; stop (price <= that position's stop price); trail activates at
+trail_start_pct (never turns off); trail exit at peak * (1 - trail_pct); timeout after timeout_h if the
price is under entry * (1 + timeout_min_gain_pct); liquidity 0 -> pulled. Exit price = polled price.
PAPER closes at once. LIVE goes "exiting" until the human taps Sold (actions.sold).
"""
from __future__ import annotations

import asyncio
import logging
import time

from desk import bus, config, db, fees, links, market

log = logging.getLogger("risk")

NO_PRICE_POLLS = 4
LABEL = {"stop": "stop", "trail": "trailing stop", "timeout": "dead money", "pulled": "liquidity pulled"}
CARD_LAST = {"stop": "Stopped", "trail": "Trailed out", "timeout": "Timed out", "pulled": "Pulled"}

_repush: dict[int, dict] = {}   # LIVE exiting positions: {pid: {"ts": first push, "done": bool}}


def _coin(mint: str) -> dict:
    return db.coin(mint) or {}


def _name(mint: str) -> str:
    c = _coin(mint)
    return c.get("name") or c.get("symbol") or mint[:6]


def _chain(mint: str) -> str:
    return _coin(mint).get("chain") or "solana"


# ---- settings --------------------------------------------------------------------------
def apply_stop_setting(new_pct: float) -> int:
    """New stop % for open positions: a tighter stop moves the stop up, a looser one never moves it down.
    New positions take risk.stop_pct at open. Returns how many positions changed."""
    n = 0
    for p in db.open_positions():
        new_price = p["entry_price"] * (1 - float(new_pct) / 100)
        if p["stop_price"] is None or new_price > p["stop_price"]:
            db.update("positions", p["id"], {"stop_pct": float(new_pct), "stop_price": new_price})
            n += 1
    return n


def _watch_stop_setting() -> None:
    cur = float(config.get("risk.stop_pct"))
    last = db.kv_get("risk_stop_applied", None)
    if last is None or float(last) != cur:
        if last is not None:
            changed = apply_stop_setting(cur)
            log.info("stop setting %s -> %s, %d open stops tightened", last, cur, changed)
        db.kv_set("risk_stop_applied", cur)


# ---- rules -------------------------------------------------------------------------------
def step(p: dict, price: float | None, liquidity: float | None, now: float | None = None) -> tuple[dict, str | None]:
    """Pure rule pass for one position. -> (fields to update, exit reason or None)."""
    g = config.get
    now = now or time.time()
    upd: dict = {}
    if price is None:
        upd["no_price_polls"] = int(p.get("no_price_polls") or 0) + 1
        return upd, ("pulled" if liquidity == 0 else None)
    entry = float(p["entry_price"])
    peak = max(float(p.get("peak_price") or entry), price)
    upd.update({"last_price": price, "peak_price": peak, "no_price_polls": 0})
    if p.get("stop_price") is not None and price <= float(p["stop_price"]):
        return upd, "stop"
    trail_on = bool(p.get("trail_active")) or peak >= entry * (1 + float(g("risk.trail_start_pct")) / 100)
    if trail_on:
        upd["trail_active"] = 1
        if price <= peak * (1 - float(g("risk.trail_pct")) / 100):
            return upd, "trail"
    if (now - float(p["opened_at"]) >= float(g("risk.timeout_h")) * 3600
            and price < entry * (1 + float(g("risk.timeout_min_gain_pct")) / 100)):
        return upd, "timeout"
    if liquidity == 0:
        return upd, "pulled"
    return upd, None


def pnl_of(p: dict, exit_price: float) -> tuple[float, float, float]:
    """-> (net pnl USD, fees USD, price move %)."""
    size = float(p["size_usd"])
    out_usd = size * exit_price / float(p["entry_price"])
    fee = fees.round_trip(size, out_usd, float(config.get("desk.fees_ref_discount")))
    move = 100 * (exit_price / float(p["entry_price"]) - 1)
    return round(out_usd - size - fee, 2), fee, move


def _money(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def close_position(pid: int, exit_price: float | None = None, reason: str | None = None) -> dict | None:
    """Close at exit_price (default: the stored trigger price, else last price). Activity + exit event."""
    p = db.q1("SELECT * FROM positions WHERE id=?", (pid,))
    if not p or p["status"] == "closed":
        return None
    reason = reason or p.get("exit_reason") or "manual"
    price = exit_price if exit_price is not None else (p.get("exit_price") or p.get("last_price") or p["entry_price"])
    pnl, fee, move = pnl_of(p, float(price))
    db.update("positions", pid, {"status": "closed", "closed_at": db.now(), "exit_price": float(price),
                                 "exit_reason": reason, "pnl_usd": pnl, "fees_usd": fee})
    name = _name(p["mint"])
    if reason == "stop":
        kind, title, sub = "stopped", f"Stopped {name}", f"Risk · -{float(p['stop_pct']):g}% stop"
    elif reason == "timeout":
        kind, title, sub = "timeout", f"Sold {name}", "Risk · dead money"
    else:
        kind, title, sub = "sold", f"Sold {name}", f"Risk · {LABEL.get(reason, 'sold by hand')}"
    db.add_activity(kind, p["mint"], "risk", title, sub, _money(pnl), f"{move:+.1f}%", p["mode"])
    bus.emit("exit", {"position_id": pid, "mint": p["mint"], "reason": reason, "price": float(price),
                      "pnlUsd": pnl, "pnlPct": round(move, 2), "mode": p["mode"]})
    _repush.pop(pid, None)
    db.kv_set("risk_last", CARD_LAST.get(reason))
    card()
    return {"position_id": pid, "pnl": pnl, "fees": fee, "move": move, "reason": reason}


def _exit_text(p: dict, reason: str, price: float) -> str:
    pnl, _, move = pnl_of(p, price)
    lead = "Stopped" if reason == "stop" else "Sell"
    return (f"{'PAPER · ' if p['mode'] == 'PAPER' else ''}{lead} {_name(p['mint'])} · {LABEL[reason]}\n"
            f"{_money(pnl)} · {move:+.1f}%")


def _sell_buttons(p: dict) -> list[tuple[str, str]]:
    return [("Sell on FOMO", links.coin_url(p["mint"], _chain(p["mint"]))), ("Sold", f"cb:sold:{p['id']}")]


async def trigger_exit(p: dict, reason: str, price: float) -> None:
    text = _exit_text(p, reason, price)
    if p["mode"] == "PAPER":
        close_position(p["id"], price, reason)
    else:
        db.update("positions", p["id"], {"status": "exiting", "exit_reason": reason, "exit_price": price})
        _repush[p["id"]] = {"ts": time.time(), "done": False}
    await market.push(text, _sell_buttons(p), p["mint"], "exit")


async def _repush_due() -> None:
    win = float(config.get("risk.confirm_window_min")) * 60
    for pid, st in list(_repush.items()):
        if st["done"] or time.time() - st["ts"] < win:
            continue
        p = db.q1("SELECT * FROM positions WHERE id=?", (pid,))
        st["done"] = True
        if p and p["status"] == "exiting":
            await market.push(_exit_text(p, p["exit_reason"], float(p["exit_price"])) + "\nStill open on your side?",
                              _sell_buttons(p), p["mint"], "exit")


def card() -> None:
    opn = db.open_positions()
    db.kv_set("risk_card", {"open": len(opn), "guarding": bool(opn), "last": db.kv_get("risk_last", None),
                            "stopPct": float(config.get("risk.stop_pct")),
                            "trailPct": float(config.get("risk.trail_pct"))})


# ---- poll --------------------------------------------------------------------------------
async def poll_once(now: float | None = None) -> None:
    _watch_stop_setting()
    positions = db.open_positions()
    by_chain: dict[str, list[dict]] = {}
    for p in positions:
        by_chain.setdefault(_chain(p["mint"]), []).append(p)
    for chain, ps in by_chain.items():
        reads = await market.market_reads(chain, sorted({p["mint"] for p in ps}))
        for p in ps:
            m = reads.get(p["mint"]) or market.empty()
            price, liq = m.get("priceUsd"), m.get("liquidityUsd")
            db.add_price(p["mint"], price, m.get("vol_m5"))
            if price is not None:
                bus.emit("price", {"mint": p["mint"], "price": price, "ts": db.now()})
            if p["status"] == "exiting":
                if price is not None:
                    db.update("positions", p["id"], {"last_price": price})
                continue
            upd, reason = step(p, price, liq, now)
            db.update("positions", p["id"], upd)
            p.update(upd)
            if upd.get("no_price_polls") == NO_PRICE_POLLS:
                await market.push(f"{_name(p['mint'])}: no price for 1 min", _sell_buttons(p), p["mint"], "warn")
            if reason:
                exit_price = price if price is not None else float(p.get("last_price") or p["entry_price"])
                await trigger_exit(p, reason, exit_price)
    await _repush_due()
    card()


async def run() -> None:
    log.info("risk up")
    card()
    while True:
        db.beat("risk")
        if not db.is_paused("risk"):
            try:
                await poll_once()
            except Exception:
                log.exception("risk poll failed")
        # wake early when the executor opens a position
        try:
            await asyncio.wait_for(bus.risk.get(), timeout=float(config.get("risk.poll_s")))
        except asyncio.TimeoutError:
            pass


async def balance_loop() -> None:
    """One balance point a minute into db.balance, emitted as "balance"."""
    while True:
        try:
            v = float(config.stack())
            t = db.now()
            db.insert("balance", {"ts": t, "value": v})
            bus.emit("balance", {"ts": t, "value": v})
        except Exception:
            log.exception("balance point failed")
        await asyncio.sleep(60)
