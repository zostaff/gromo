"""Copy tab: the copy bot's trades (data/copybot.db, read only) joined with what the desk thinks of each coin.

The copy bot is a separate process. The desk only reads its db file, never writes it, never reads the
FOMO token value (only whether one is set).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import db, links

COPY_DB = Path(os.environ.get("COPYBOT_DB", db.ROOT / "data" / "copybot.db"))
STALE_S = 600


def _ro() -> sqlite3.Connection | None:
    if not COPY_DB.is_file():
        return None
    c = sqlite3.connect(f"file:{COPY_DB}?mode=ro", uri=True, timeout=2)
    c.row_factory = sqlite3.Row
    return c


def desk_view(address: str | None) -> dict:
    """What the desk thinks of a coin: cleared / rejected / watching / unseen."""
    if not address:
        return {"verdict": "unseen", "label": None}
    c = db.q1("SELECT mint FROM coins WHERE lower(mint)=lower(?)", (address,))
    if not c:
        return {"verdict": "unseen", "label": None}
    mint = c["mint"]
    rej = db.q1("SELECT subtitle, ts FROM activity WHERE mint=? AND kind='rejected' ORDER BY ts DESC LIMIT 1", (mint,))
    kill = db.q1("SELECT reason, ts FROM scans WHERE mint=? AND verdict='kill' ORDER BY ts DESC LIMIT 1", (mint,))
    from .api import latest_audit, shield
    a = latest_audit(mint)
    a_ts = None
    if a and a.get("passed"):
        r = db.q1("SELECT MAX(ts) AS ts FROM audits WHERE mint=?", (mint,))
        a_ts = r["ts"] if r else None
    cands = []
    if rej:
        cands.append((rej["ts"], "rejected", rej["subtitle"]))
    if kill:
        cands.append((kill["ts"], "rejected", f"Scanner · {kill['reason']}"))
    if a_ts:
        cands.append((a_ts, "cleared", f"{shield(a)} cleared"))
    if not cands:
        return {"verdict": "watching", "label": "Scanner · waiting"}
    _, verdict, label = max(cands, key=lambda x: x[0] or 0)
    return {"verdict": verdict, "label": label}


def _has_token(c: sqlite3.Connection) -> bool:
    if os.environ.get("FOMO_TOKEN", "").strip():
        return True
    r = c.execute("SELECT value FROM kv WHERE key='fomo_token'").fetchone()
    return bool(r and r["value"] and r["value"] not in ('""', "null"))


def feed() -> dict:
    out: dict = {"state": "no_token", "verified": False, "lastPoll": None, "traders": [], "hot": [], "trades": []}
    try:
        from copybot import fields as cf
        out["verified"] = bool(cf.FOMO_VERIFIED)
    except ImportError:
        pass
    c = _ro()
    if c is None:
        return out
    try:
        now = db.now()
        traders = [dict(r) for r in c.execute("SELECT id, handle, name, last_poll FROM traders ORDER BY handle")]
        stats = {r["trader_id"]: (r["n"], r["last"]) for r in c.execute(
            "SELECT trader_id, COUNT(*) AS n, MAX(ts) AS last FROM trades WHERE ts>=? GROUP BY trader_id", (now - 86400,))}
        last_poll = max((t["last_poll"] or 0 for t in traders), default=0) or None
        out["lastPoll"] = last_poll
        if not _has_token(c):
            out["state"] = "no_token"
        elif not traders:
            out["state"] = "no_traders"
        elif not last_poll or now - last_poll > STALE_S:
            out["state"] = "stale"
        else:
            out["state"] = "running"
        out["traders"] = [{"id": t["id"], "handle": t["handle"], "name": t["name"],
                           "trades24h": stats.get(t["id"], (0, None))[0], "lastTs": stats.get(t["id"], (0, None))[1],
                           "profileUrl": links.profile_url(t["handle"]) if t["handle"] else None} for t in traders]
        views: dict[str, dict] = {}

        def view(addr: str | None) -> dict:
            k = (addr or "").lower()
            if k not in views:
                views[k] = desk_view(addr)
            return views[k]

        for r in c.execute("SELECT * FROM trades ORDER BY ts DESC LIMIT 50"):
            out["trades"].append({"id": r["swap_id"], "handle": r["handle"], "ts": r["ts"], "side": r["side"],
                                  "address": r["address"], "chain": r["chain"], "symbol": r["symbol"],
                                  "name": r["name"], "usd": r["usd"], "mcap": r["mcap"],
                                  "coinUrl": links.coin_url(r["address"], r["chain"]) if r["address"] else links.signup_url(),
                                  "desk": view(r["address"])})
        hot: dict[str, dict] = {}
        for r in c.execute("SELECT * FROM trades WHERE side='buy' AND ts>=? ORDER BY ts DESC", (now - 86400,)):
            if not r["address"]:
                continue
            h = hot.setdefault(r["address"].lower(), {"address": r["address"], "chain": r["chain"], "symbol": r["symbol"],
                                                     "name": r["name"], "handles": [], "usd": 0.0, "usdKnown": False,
                                                     "lastTs": r["ts"]})
            if r["handle"] not in h["handles"]:
                h["handles"].append(r["handle"])
            if r["usd"] is not None:
                h["usd"] += r["usd"]
                h["usdKnown"] = True
        rows = []
        for h in hot.values():
            if len(h["handles"]) < 2:
                continue
            rows.append({"address": h["address"], "chain": h["chain"], "symbol": h["symbol"], "name": h["name"],
                         "buyers": len(h["handles"]), "handles": h["handles"],
                         "usd": round(h["usd"], 2) if h["usdKnown"] else None, "lastTs": h["lastTs"],
                         "coinUrl": links.coin_url(h["address"], h["chain"]), "desk": view(h["address"])})
        out["hot"] = sorted(rows, key=lambda x: (-x["buyers"], -(x["lastTs"] or 0)))[:12]
    finally:
        c.close()
    return out
