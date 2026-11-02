"""Button callbacks, shared by Telegram and the HTTP API. Never place an order: they record what the human did.

bought(signal_id): the human bought in FOMO -> open the position at the signal price (LIVE).
sold(position_id): the human sold in FOMO -> close at the trigger price (or the last polled price).
skip(signal_id): do not signal this coin again until the next report.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from desk import config, db

log = logging.getLogger("actions")


def _signal(signal_id: int) -> dict | None:
    return db.q1("SELECT * FROM signals WHERE id=?", (int(signal_id),))


async def bought(signal_id: int) -> dict:
    from desk.agents import executor
    s = _signal(signal_id)
    if not s:
        return {"ok": False, "message": "no such signal"}
    if s["status"] == "bought":
        return {"ok": True, "message": "already open"}
    if s["status"] != "sent":
        return {"ok": False, "message": f"signal is {s['status']}"}
    pid = executor.open_position(s)
    executor.card(state="filled", progress=100)
    return {"ok": True, "message": "position open", "position_id": pid}


async def sold(position_id: int) -> dict:
    from desk.agents import risk
    p = db.q1("SELECT * FROM positions WHERE id=?", (int(position_id),))
    if not p:
        return {"ok": False, "message": "no such position"}
    if p["status"] == "closed":
        return {"ok": True, "message": "already closed"}
    r = risk.close_position(p["id"])
    return {"ok": True, "message": "position closed", **(r or {})}


def next_report_ts(now: datetime | None = None) -> float:
    """Next desk.report_time (HH:MM, machine local time)."""
    now = now or datetime.now().astimezone()
    try:
        hh, mm = (int(x) for x in str(config.get("desk.report_time")).split(":", 1))
    except ValueError:
        hh, mm = 8, 0
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t.timestamp()


async def skip(signal_id: int) -> dict:
    s = _signal(signal_id)
    if not s:
        return {"ok": False, "message": "no such signal"}
    until = next_report_ts()
    db.x("INSERT INTO skips(mint,until) VALUES(?,?) ON CONFLICT(mint) DO UPDATE SET until=excluded.until",
         (s["mint"], until))
    if s["status"] == "sent":
        db.update("signals", s["id"], {"status": "skipped", "reason": "you skipped it"})
    log.info("skip %s until %s", s["mint"], until)
    return {"ok": True, "message": "skipped until the next report"}
