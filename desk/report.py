"""Morning report, nights, mode switch and desk-health.

- report_loop(): at desk.report_time (local time) builds the night's HTML report into
  data/reports/YYYY-MM-DD.html (served at /reports/<date>.html?t=APP_TOKEN), ends the night,
  starts a new one, clears db.skips and pushes the link.
- health_loop(sup): every 60s checks heartbeats; a dead agent task or a heartbeat older than
  2 min is restarted and pushed. More than 10 websocket reconnects in an hour is pushed.
  Sources report reconnects with `note_reconnect("pumpportal")`.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config, db, links

log = logging.getLogger("report")

AGENTS = ("scanner", "auditor", "analyst", "executor", "risk")
REPORTS_DIR = db.ROOT / "data" / "reports"


# ---- nights / mode ----------------------------------------------------------------
def ensure_night() -> tuple[dict, bool]:
    """Current night, starting one if none. Returns (night, started_now)."""
    n = db.current_night()
    if n:
        return n, False
    return db.start_night(config.stack(), config.get("desk.mode")), True


def night_start_ts() -> float:
    n = db.current_night()
    return float(n["started_at"]) if n else db.now() - 86400


def end_night(report_path: str | None = None) -> dict | None:
    n = db.current_night()
    if n:
        db.x("UPDATE nights SET ended_at=?, report_path=? WHERE id=?", (db.now(), report_path, n["id"]))
    return n


def full_paper_nights() -> int:
    r = db.q1("SELECT COUNT(*) AS c FROM nights WHERE mode='PAPER' AND ended_at IS NOT NULL")
    return int(r["c"]) if r else 0


def switch_mode(mode: str, confirm: str | None, who: str) -> tuple[bool, str]:
    """PAPER always allowed. LIVE needs confirm == "LIVE" and at least 1 full PAPER night."""
    mode = (mode or "").strip().upper()
    if mode not in ("PAPER", "LIVE"):
        return False, "mode is PAPER or LIVE"
    old = config.get("desk.mode")
    if mode == old:
        return True, f"already {mode}"
    if mode == "LIVE":
        if confirm != "LIVE":
            return False, "type LIVE to confirm"
        if full_paper_nights() < 1:
            return False, "LIVE needs at least 1 full PAPER night first"
    key, old, new = config.set_value("desk.mode", mode)
    db.save_setting(key, old, new, who)
    end_night(None)
    n = db.start_night(config.stack(), new)
    tc = config.ticket_check(config.stack())
    msg = f"mode {old} -> {new}, stack ${n['start_balance']:,.2f}"
    if tc["message"]:
        msg += f", {tc['message']}"
    from . import bus
    bus.emit("balance", {"balance": config.stack(), "mode": new})
    return True, msg


# ---- report ---------------------------------------------------------------------------
def _a(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'


def _coin_link(mint: str) -> str:
    c = db.coin(mint) or {}
    label = c.get("symbol") or c.get("name") or (mint[:6] + "..")
    return _a(links.coin_url(mint, c.get("chain") or "solana"), label)


def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def collect(since: float, until: float) -> dict[str, Any]:
    closed = db.q("SELECT * FROM positions WHERE status='closed' AND closed_at>=? AND closed_at<? "
                  "ORDER BY pnl_usd", (since, until))
    reds = [p for p in closed if (p["pnl_usd"] or 0) < 0]
    greens = sorted([p for p in closed if (p["pnl_usd"] or 0) >= 0], key=lambda p: -(p["pnl_usd"] or 0))
    kills = Counter(r["reason"] or "n/a" for r in db.q(
        "SELECT reason FROM scans WHERE verdict='kill' AND ts>=? AND ts<?", (since, until)))
    # auditor refusals: per mint, the first failing layer of its latest audit
    fails = db.q("SELECT mint, layer, name, MAX(ts) AS ts FROM audits WHERE pass=0 AND ts>=? AND ts<? "
                 "GROUP BY mint, layer ORDER BY mint, layer", (since, until))
    seen: set[str] = set()
    refusals: Counter = Counter()
    for f in fails:
        if f["mint"] in seen:
            continue
        seen.add(f["mint"])
        refusals[f"{f['layer']}. {f['name']}"] += 1
    rejects: Counter = Counter()
    for v in db.q("SELECT reason FROM verdicts WHERE pass=0 AND ts>=? AND ts<?", (since, until)):
        rule = (v["reason"] or "n/a").split(",")[0].strip().split(" ")[0] or "n/a"
        rejects[rule] += 1
    g = db.q1("SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd),0) AS cost FROM verdicts "
              "WHERE model LIKE 'grok%' AND ts>=? AND ts<?", (since, until)) or {"calls": 0, "cost": 0}
    fees = sum(p["fees_usd"] or 0 for p in closed)
    pnl = sum(p["pnl_usd"] or 0 for p in closed)
    return {"reds": reds, "greens": greens, "kills": kills, "refusals": refusals, "rejects": rejects,
            "grok_calls": int(g["calls"]), "grok_cost": float(g["cost"]), "fees": fees, "pnl": pnl,
            "open": db.open_positions()}


def render(night: dict | None, since: float, until: float, title_date: str) -> str:
    d = collect(since, until)
    mode = (night or {}).get("mode") or config.get("desk.mode")
    start_bal = (night or {}).get("start_balance")

    def pos_rows(rows: list[dict]) -> str:
        if not rows:
            return "<p class=muted>none</p>"
        out = []
        for p in rows:
            pct = (p["exit_price"] / p["entry_price"] - 1) * 100 if p["exit_price"] and p["entry_price"] else None
            out.append(f"<tr><td>{_coin_link(p['mint'])}</td><td>{_money(p['pnl_usd'])}</td>"
                       f"<td>{'n/a' if pct is None else f'{pct:+.1f}%'}</td>"
                       f"<td>{html.escape(p['exit_reason'] or 'n/a')}</td></tr>")
        return "<table>" + "".join(out) + "</table>"

    def counter(c: Counter) -> str:
        if not c:
            return "<p class=muted>none</p>"
        return "<table>" + "".join(f"<tr><td>{html.escape(str(k))}</td><td>{v}</td></tr>"
                                   for k, v in c.most_common()) + "</table>"

    opened = ""
    if d["open"]:
        opened = "<h2>Still open</h2><ul>" + "".join(
            f"<li>{_coin_link(p['mint'])} ${p['size_usd']:,.2f}</li>" for p in d["open"]) + "</ul>"
    su = links.signup_url()
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>GROKHOOD Desk report {title_date}</title>
<style>body{{font:15px -apple-system,system-ui,sans-serif;background:#0b0b0c;color:#eee;max-width:640px;margin:auto;padding:16px}}
a{{color:#cdff00}}table{{width:100%;border-collapse:collapse}}td{{padding:6px 4px;border-bottom:1px solid #222}}
.muted{{color:#888}}h2{{font-size:17px;margin-top:28px}}.red{{color:#ff5a5a}}.green{{color:#3ddc84}}</style></head>
<body><h1>Night report {title_date}</h1>
<p>{html.escape(mode)} · start {('$' + format(start_bal, ',.2f')) if start_bal is not None else 'n/a'}
 · end ${config.stack():,.2f} · realized {_money(d['pnl'])}</p>
<h2 class=red>Reds</h2>{pos_rows(d['reds'])}
<h2 class=green>Greens</h2>{pos_rows(d['greens'])}
{opened}
<h2>Scanner kills by reason</h2>{counter(d['kills'])}
<h2>Auditor refusals by layer</h2>{counter(d['refusals'])}
<h2>Analyst rejects by rule</h2>{counter(d['rejects'])}
<h2>Costs</h2><table><tr><td>Grok</td><td>{d['grok_calls']} calls, ${d['grok_cost']:.4f}</td></tr>
<tr><td>FOMO fees ({html.escape(mode)})</td><td>${d['fees']:.2f}</td></tr></table>
<p class=muted style="margin-top:32px">New to FOMO? {_a(su, su)}</p></body></html>"""


def report_url(date: str) -> str:
    base = os.environ.get("PUBLIC_URL", "").rstrip("/") or \
        f"http://{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('PORT', '8890')}"
    return f"{base}/reports/{date}.html?t={os.environ.get('APP_TOKEN', '')}"


def write_report(end: bool) -> tuple[str, Path]:
    """Build the report for the current night. end=True closes the night and opens a new one."""
    night = db.current_night()
    since = float(night["started_at"]) if night else db.now() - 86400
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{date}.html"
    path.write_text(render(night, since, db.now(), date), encoding="utf-8")
    if end:
        end_night(str(path))
        db.start_night(config.stack(), config.get("desk.mode"))
        db.x("DELETE FROM skips")
        db.kv_set("last_report_date", date)
    return date, path


async def run_morning() -> str:
    from . import nightpost, notify
    night = db.current_night()
    since = float(night["started_at"]) if night else db.now() - 86400
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    post = None
    try:
        post = nightpost.build(since, date, REPORTS_DIR)      # before the night ends: its numbers
    except Exception:  # noqa: BLE001
        log.exception("night post failed")
    date, _ = write_report(end=True)
    url = report_url(date)
    await notify.push(f"Morning report {date} is ready. New night started.", [("Open report", url)],
                      kind="report")
    if post:
        await nightpost.send(post, url)
    return url


def _next_report_at(now: datetime) -> datetime:
    try:
        hh, mm = (int(x) for x in str(config.get("desk.report_time")).split(":", 1))
    except ValueError:
        hh, mm = 8, 0
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


async def report_loop() -> None:
    while True:
        now = datetime.now().astimezone()
        target = _next_report_at(now)
        # sleep in short chunks so a changed report_time takes effect
        await asyncio.sleep(min(60.0, max(1.0, (target - now).total_seconds())))
        now = datetime.now().astimezone()
        if now >= target and db.kv_get("last_report_date") != now.strftime("%Y-%m-%d"):
            try:
                await run_morning()
            except Exception:
                log.exception("morning report failed")


# ---- desk health -----------------------------------------------------------------------
def note_reconnect(source: str) -> None:
    """Sources call this on every websocket reconnect."""
    now = time.time()
    rows = [t for t in (db.kv_get("ws_reconnects", []) or []) if now - t < 3600]
    rows.append(now)
    db.kv_set("ws_reconnects", rows)
    log.info("websocket reconnect: %s", source)


async def health_check(sup: Any) -> list[str]:
    """One pass. `sup` exposes started_at(name)->float|None, done(name)->bool, restart(name)."""
    from . import notify
    out = []
    now = db.now()
    beats = {r["agent"]: r["ts"] for r in db.q("SELECT agent, ts FROM heartbeats")}
    for a in AGENTS:
        started = sup.started_at(a)
        if started is None or not getattr(sup, "available", lambda n: True)(a):
            continue
        last = beats.get(a)
        ref = max(last or 0, started)
        dead = sup.done(a)
        stale = now - ref > 120 and not db.is_paused(a)
        if dead or stale:
            mins = max(1, int((now - (last or started)) // 60))
            sup.restart(a)
            out.append(a)
            log.warning("restarted %s (dead=%s, down %d min)", a, dead, mins)
            await notify.push(f"Restarted {a.capitalize()}, down {mins} min", [], kind="health")
    rec = [t for t in (db.kv_get("ws_reconnects", []) or []) if now - t < 3600]
    if len(rec) > 10 and now - float(db.kv_get("ws_alert_ts", 0) or 0) > 3600:
        db.kv_set("ws_alert_ts", now)
        await notify.push(f"Websocket reconnected {len(rec)} times in the last hour", [], kind="health")
        out.append("websocket")
    return out


async def health_loop(sup: Any) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await health_check(sup)
        except Exception:
            log.exception("health check failed")
