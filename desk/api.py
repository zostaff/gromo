"""HTTP API + SSE + static web app. Shapes follow docs/ARCHITECTURE.md exactly.

Auth: every /api/* call needs `Authorization: Bearer APP_TOKEN` (SSE also accepts ?t=),
/reports/* needs ?t=. Constant-time compare. No APP_TOKEN in env = everything 401.
"""
from __future__ import annotations

import asyncio
import hmac
import importlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import bus, config, copyfeed, db, links, nightpost, replay, report

log = logging.getLogger("api")

WEB = Path(__file__).resolve().parent / "web"
AGENTS = report.AGENTS
NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
RANGES = {"LIVE": 30 * 60, "1H": 3600, "4H": 4 * 3600, "1D": 86400, "1W": 7 * 86400, "ALL": None}

app = FastAPI(title="GROMO", docs_url=None, redoc_url=None, openapi_url=None)


# ---- auth -------------------------------------------------------------------------------
def _token_ok(given: str | None) -> bool:
    want = os.environ.get("APP_TOKEN", "")
    if not want or not given:
        return False
    return hmac.compare_digest(given.encode(), want.encode())


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/reports/"):
        h = request.headers.get("authorization", "")
        given = h[7:].strip() if h.lower().startswith("bearer ") else None
        if given is None and (path == "/api/stream" or path.startswith("/reports/")):
            given = request.query_params.get("t")
        if not _token_ok(given):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


def err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


# ---- helpers ------------------------------------------------------------------------------
def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _extra(c: dict | None) -> dict:
    try:
        e = json.loads((c or {}).get("extra") or "{}")
        return e if isinstance(e, dict) else {}
    except ValueError:
        return {}


def coin_link(mint: str, chain: str | None) -> str:
    return links.coin_url(mint, chain or "solana")


def last_price(mint: str) -> float | None:
    r = db.q1("SELECT price FROM prices WHERE mint=? ORDER BY ts DESC LIMIT 1", (mint,))
    return float(r["price"]) if r else None


def agent_status(name: str, beats: dict[str, float]) -> str:
    if db.is_paused(name):
        return "paused"
    b = beats.get(name)
    return "running" if b is not None and db.now() - b <= 120 else "down"


def _count(sql: str, args: tuple = ()) -> int:
    r = db.q1(sql, args)
    return int(list(r.values())[0] or 0) if r else 0


def build_cards(since: float) -> list[dict]:
    beats = {r["agent"]: r["ts"] for r in db.q("SELECT agent, ts FROM heartbeats")}
    kv = {a: (db.kv_get(f"{a}_card") or {}) for a in AGENTS}
    out = []

    # scanner
    pools = _count("SELECT COUNT(*) FROM scans WHERE ts>=?", (since,))
    passed = _count("SELECT COUNT(*) FROM scans WHERE ts>=? AND verdict='pass'", (since,))
    feed = kv["scanner"].get("feed")
    if not isinstance(feed, list):
        feed = [{"name": r["name"], "symbol": r["symbol"], "mint": r["mint"], "chain": r["chain"] or "solana",
                 "verdict": r["verdict"], "reason": r["reason"], "liq": r["liquidity_usd"]}
                for r in db.q("SELECT s.mint, s.verdict, s.reason, s.liquidity_usd, c.name, c.symbol, c.chain "
                              "FROM scans s LEFT JOIN coins c ON c.mint=s.mint ORDER BY s.id DESC LIMIT 6")]
    out.append(("scanner", pools, f"{pools} pools · {passed} passed", {"pools": pools, "feed": feed[:6]}))

    # auditor
    taken = _count("SELECT COUNT(DISTINCT mint) FROM audits WHERE ts>=?", (since,))
    rejected = _count("SELECT COUNT(DISTINCT mint) FROM audits WHERE ts>=? AND pass=0", (since,))
    k = kv["auditor"]
    out.append(("auditor", taken, f"{taken} taken apart · {rejected} rejected",
                {"takenApart": taken, "rejected": rejected, "current": k.get("current"), "last": k.get("last")}))

    # analyst
    scored = _count("SELECT COUNT(*) FROM verdicts WHERE ts>=?", (since,))
    ok = _count("SELECT COUNT(*) FROM verdicts WHERE ts>=? AND pass=1", (since,))
    k = kv["analyst"]
    out.append(("analyst", scored, f"{scored} scored · {ok} passed",
                {"scoring": k.get("scoring"), "scored": scored, "mode": config.get("analyst.mode")}))

    # executor
    trades = _count("SELECT COUNT(*) FROM positions WHERE opened_at>=?", (since,))
    last = db.q1("SELECT size_usd FROM positions ORDER BY opened_at DESC LIMIT 1")
    k = kv["executor"]
    out.append(("executor", trades, f"{trades} trades tonight",
                {"trades": trades, "lastSize": last["size_usd"] if last else None,
                 "stackPct": float(config.get("executor.stack_pct")), "state": k.get("state", "idle"),
                 "progress": k.get("progress", 0), "name": k.get("name")}))

    # risk
    n_open = len(db.open_positions())
    k = kv["risk"]
    out.append(("risk", n_open, f"guarding {n_open} open" if n_open else "nothing open",
                {"open": n_open, "guarding": n_open > 0, "last": k.get("last"),
                 "stopPct": float(config.get("risk.stop_pct")), "trailPct": float(config.get("risk.trail_pct"))}))

    return [{"name": n, "status": agent_status(n, beats), "count": c,
             "subtitle": kv[n].get("subtitle") or sub, "card": card} for n, c, sub, card in out]


def balance_now() -> float:
    return round(float(config.stack()), 2)


# ---- endpoints --------------------------------------------------------------------------
@app.get("/api/state")
def state() -> dict:
    night = db.current_night()
    bal = balance_now()
    start = float(night["start_balance"]) if night and night["start_balance"] else bal
    since = float(night["started_at"]) if night else db.now() - 86400
    return {"balance": bal, "todayPct": round((bal / start - 1) * 100, 2) if start else 0.0,
            "multiplier": round(bal / start, 4) if start else 1.0, "mode": config.get("desk.mode"),
            "nightStart": start, "online": True, "updatedAt": db.now(),
            "unread": int(db.kv_get("unread", 0) or 0), "handle": config.get("desk.fomo_handle"),
            "agents": build_cards(since)}


@app.post("/api/read")
def mark_read() -> dict:
    db.kv_set("unread", 0)
    return {"unread": 0}


@app.get("/api/chart")
def chart(range: str = "LIVE") -> Response:  # noqa: A002 - query name is part of the contract
    r = range.upper()
    if r not in RANGES:
        return err("range is one of " + ", ".join(RANGES))
    now = db.now()
    since = now - RANGES[r] if RANGES[r] else 0.0
    rows = db.q("SELECT ts, value FROM balance WHERE ts>=? ORDER BY ts", (since,))
    step = max(1, len(rows) // 600)
    points = [[x["ts"], x["value"]] for x in rows[::step]]
    if rows and (not points or points[-1][0] != rows[-1]["ts"]):
        points.append([rows[-1]["ts"], rows[-1]["value"]])
    bal = balance_now()
    points.append([now, bal])
    before = db.q1("SELECT value FROM balance WHERE ts<? ORDER BY ts DESC LIMIT 1", (since,))
    start = float(before["value"]) if before else (points[0][1] if points else bal)
    marks = []
    for p in db.q("SELECT p.*, c.name, c.symbol FROM positions p LEFT JOIN coins c ON c.mint=p.mint "
                  "WHERE p.opened_at>=? OR p.closed_at>=?", (since, since)):
        label = p["symbol"] or p["name"] or p["mint"][:6]
        if p["opened_at"] and p["opened_at"] >= since:
            marks.append({"ts": p["opened_at"], "kind": "buy", "label": f"Bought {label}", "mint": p["mint"]})
        if p["status"] == "closed" and p["closed_at"] and p["closed_at"] >= since and p["pnl_usd"] is not None:
            pnl = float(p["pnl_usd"])
            if pnl >= 0:
                marks.append({"ts": p["closed_at"], "kind": "win", "label": f"+${pnl:,.2f}", "mint": p["mint"]})
            else:
                pct = (p["exit_price"] / p["entry_price"] - 1) * 100 if p["exit_price"] and p["entry_price"] else None
                lab = (f"{pct:.0f}% · " if pct is not None else "") + f"-${abs(pnl):,.2f}"
                marks.append({"ts": p["closed_at"], "kind": "loss", "label": lab, "mint": p["mint"]})
    marks.sort(key=lambda m: m["ts"])
    return JSONResponse({"points": points, "start": start, "marks": marks})


@app.get("/api/activity")
def activity(before: int | None = None) -> dict:
    sql = ("SELECT a.*, c.chain, c.image FROM activity a LEFT JOIN coins c ON c.mint=a.mint "
           + ("WHERE a.id<? " if before else "") + "ORDER BY a.id DESC LIMIT 31")
    rows = db.q(sql, (before,) if before else ())
    nxt = rows[29]["id"] if len(rows) > 30 else None
    rows = rows[:30]
    for r in rows:
        r["chain"] = r.get("chain") or "solana"
        r["coinUrl"] = coin_link(r["mint"], r["chain"]) if r.get("mint") else None
    return {"rows": rows, "next": nxt}


def latest_audit(mint: str) -> dict | None:
    """Latest audit of a mint: auditor JSON from kv `audit:<mint>` if the auditor stored it,
    else rebuilt from db.audits (rows since the latest layer-1 row)."""
    stored = db.kv_get(f"audit:{mint}")
    if isinstance(stored, dict) and stored.get("layers") is not None:
        return stored
    first = db.q1("SELECT MAX(ts) AS ts FROM audits WHERE mint=? AND layer=1", (mint,))
    t0 = first["ts"] if first and first["ts"] is not None else None
    rows = db.q("SELECT * FROM audits WHERE mint=? AND ts>=? ORDER BY id", (mint, t0 or 0))
    if not rows:
        return None
    by: dict[int, dict] = {}
    for r in rows:
        by[r["layer"]] = r
    layers = [{"n": n, "name": r["name"], "pass": None if r["pass"] is None else bool(r["pass"]),
               "value": r["value_text"], "source": r["source"]} for n, r in sorted(by.items())]
    failed = [l for l in layers if l["pass"] is False]
    c = db.coin(mint) or {}
    e = _extra(c)
    return {"agent": "auditor", "mint": mint, "chain": c.get("chain") or "solana",
            "ts": datetime.fromtimestamp(rows[-1]["ts"]).astimezone().isoformat(),
            "passed": not failed and len(layers) >= 8 and all(l["pass"] for l in layers),
            "layersChecked": sum(1 for l in layers if l["pass"] is not None), "layers": layers,
            "holders": c.get("holders"),
            "reason": f"{failed[0]['name']}: {failed[0]['value']}" if failed else None,
            "bundled": e.get("bundled"), "safetyScore": e.get("safetyScore")}


def shield(a: dict | None) -> str | None:
    if not a or not a.get("layers"):
        return None
    ok = sum(1 for l in a["layers"] if l.get("pass") is True)
    return f"{ok}/{len(a['layers'])}"


def candles(mint: str, since: float) -> list[list]:
    rows = db.q("SELECT ts, price, volume_m5 FROM prices WHERE mint=? AND ts>=? ORDER BY ts", (mint, since))
    out: dict[int, list] = {}
    prev_vol: float | None = None
    for r in rows:
        m = int(r["ts"] // 60) * 60
        p = float(r["price"])
        v = r["volume_m5"]
        dv = 0.0
        if v is not None:
            if prev_vol is not None and v > prev_vol:
                dv = float(v) - prev_vol
            prev_vol = float(v)
        c = out.get(m)
        if c is None:
            out[m] = [m, p, p, p, p, dv]
        else:
            c[2] = max(c[2], p)
            c[3] = min(c[3], p)
            c[4] = p
            c[5] += dv
    return [[c[0], c[1], c[2], c[3], c[4], round(c[5], 2)] for c in sorted(out.values())][-1440:]


@app.get("/api/coin/{mint}")
def coin(mint: str) -> Response:
    c = db.coin(mint)
    if not c:
        return err("unknown coin", 404)
    e = _extra(c)
    chain = c.get("chain") or "solana"
    price = last_price(mint)
    born = c.get("migrated_at") or c.get("first_seen")
    a = latest_audit(mint)
    p = db.q1("SELECT * FROM positions WHERE mint=? AND status IN ('open','exiting') "
              "ORDER BY opened_at DESC LIMIT 1", (mint,))
    pos = None
    exit_line = None
    since_entry = None
    if p:
        cur = price or p.get("last_price") or p["entry_price"]
        trail = None
        if p["trail_active"] and p["peak_price"]:
            trail = p["peak_price"] * (1 - float(config.get("risk.trail_pct")) / 100)
        pnl_pct = (cur / p["entry_price"] - 1) * 100 if p["entry_price"] else None
        pos = {"sizeUsd": p["size_usd"], "entryPrice": p["entry_price"], "stopPrice": p["stop_price"],
               "trailPrice": trail, "trailActive": bool(p["trail_active"]),
               "timeoutAt": p["opened_at"] + float(config.get("risk.timeout_h")) * 3600,
               "pnlUsd": round(p["size_usd"] * pnl_pct / 100, 2) if pnl_pct is not None else None,
               "pnlPct": round(pnl_pct, 2) if pnl_pct is not None else None, "mode": p["mode"]}
        exit_line = trail if trail is not None else p["stop_price"]
        since_entry = pos["pnlPct"]
    t0 = (p["opened_at"] - 3600) if p else db.now() - 6 * 3600
    safety = e.get("safetyScore") if e.get("safetyScore") is not None else (a or {}).get("safetyScore")
    return JSONResponse({
        "mint": mint, "chain": chain, "name": c.get("name"), "symbol": c.get("symbol"), "image": c.get("image"),
        "ageH": round((db.now() - born) / 3600, 2) if born else None, "price": price,
        "sinceEntryPct": since_entry, "shield": shield(a),
        "marketCap": _num(e.get("marketCap")), "liquidity": _num(e.get("liquidityUsd", e.get("liquidity"))),
        "volume24h": _num(e.get("volume24h")), "holders": c.get("holders"),
        "candles": candles(mint, t0), "position": pos, "exitLine": exit_line,
        "coinUrl": coin_link(mint, chain),
        "grokhoodUrl": links.grokhood_url(mint) if chain == "robinhood" else None,
        "safetyScore": int(safety) if isinstance(safety, (int, float)) else None})


@app.get("/api/audit/{mint}")
def audit(mint: str) -> dict:
    a = latest_audit(mint)
    if not a:
        return {"layers": []}
    c = db.coin(mint) or {}
    chain = c.get("chain") or a.get("chain") or "solana"
    return {**a, "name": c.get("name"), "symbol": c.get("symbol"), "chain": chain,
            "coinUrl": coin_link(mint, chain)}


def decisions_rows(name: str) -> list[dict]:
    if name == "scanner":
        rows = db.q("SELECT ts, mint, verdict, reason FROM scans ORDER BY id DESC LIMIT 20")
    elif name == "auditor":
        rows = []
        for r in db.q("SELECT mint, MAX(ts) AS ts FROM audits GROUP BY mint ORDER BY ts DESC LIMIT 20"):
            a = latest_audit(r["mint"]) or {}
            rows.append({"ts": r["ts"], "mint": r["mint"], "verdict": "pass" if a.get("passed") else "reject",
                         "reason": a.get("reason")})
    elif name == "analyst":
        rows = [{"ts": r["ts"], "mint": r["mint"], "verdict": "pass" if r["pass"] else "reject",
                 "reason": r["reason"] or (f"score {r['score']}" if r["score"] is not None else None)}
                for r in db.q("SELECT * FROM verdicts ORDER BY id DESC LIMIT 20")]
    elif name == "executor":
        rows = [{"ts": r["ts"], "mint": r["mint"], "verdict": r["status"], "reason": r["reason"]}
                for r in db.q("SELECT * FROM signals ORDER BY id DESC LIMIT 20")]
    else:
        rows = [{"ts": r["closed_at"] or r["opened_at"], "mint": r["mint"],
                 "verdict": "closed" if r["status"] == "closed" else "guarding", "reason": r["exit_reason"]}
                for r in db.q("SELECT * FROM positions ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT 20")]
    for r in rows:
        c = db.coin(r["mint"]) or {}
        r["name"] = c.get("symbol") or c.get("name")
        r["coinUrl"] = coin_link(r["mint"], c.get("chain"))
    return rows


@app.get("/api/agents/{name}/decisions")
def decisions(name: str) -> Response:
    if name not in AGENTS:
        return err("unknown agent", 404)
    return JSONResponse({"rows": decisions_rows(name)})


def grok_today() -> dict:
    try:
        an = importlib.import_module("desk.agents.analyst")
        if hasattr(an, "grok_today"):
            return an.grok_today()
    except Exception:  # noqa: BLE001
        pass
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    r = db.q1("SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd),0) AS cost FROM verdicts "
              "WHERE model LIKE 'grok%' AND ts>=?", (midnight,)) or {"calls": 0, "cost": 0}
    return {"calls": int(r["calls"]), "costUsd": round(float(r["cost"]), 4)}


@app.get("/api/account")
def account() -> dict:
    h = config.get("desk.fomo_handle") or ""
    return {"mode": config.get("desk.mode"), "stack": balance_now(), "handle": h,
            "profileUrl": links.profile_url(h) if h else None, "signupUrl": links.signup_url(),
            "telegram": bool(os.environ.get("TG_TOKEN")) and db.kv_get("owner_chat_id") is not None,
            "webpush": _count("SELECT COUNT(*) FROM push_subs") > 0,
            "vapidPublicKey": os.environ.get("VAPID_PUBLIC_KEY") or None,
            "judge": config.get("analyst.mode"), "grokToday": grok_today()}


@app.get("/api/settings")
def settings_get() -> dict:
    return config.all_settings()


async def apply_setting(key: str, value: Any, who: str) -> tuple[str, Any, Any]:
    """Validate + persist + side effects. Raises config.SettingError."""
    k = config.resolve(key)
    if k == "desk.mode":
        raise config.SettingError("change mode with /api/mode (LIVE needs confirm)")
    k, old, new = config.set_value(k, value)
    db.save_setting(k, old, new, who)
    if k == "risk.stop_pct":
        try:
            mod = importlib.import_module("desk.agents.risk")
            fn = getattr(mod, "apply_stop_setting", None)
            if fn:
                res = fn(new)
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            log.exception("apply_stop_setting failed")
    bus.emit("agent", {"setting": k, "old": old, "new": new})
    return k, old, new


@app.post("/api/settings")
async def settings_post(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        return err("body must be JSON {key, value}")
    if not isinstance(body, dict) or "key" not in body or "value" not in body:
        return err("body must be JSON {key, value}")
    try:
        k, old, new = await apply_setting(str(body["key"]), body["value"], "app")
    except config.SettingError as e:
        return err(str(e))
    return JSONResponse({"key": k, "old": old, "new": new})


def set_agent_paused(name: str, paused: bool) -> tuple[bool, str]:
    if name not in AGENTS:
        return False, "unknown agent"
    if paused and name == "risk" and db.open_positions():
        return False, "Risk cannot pause while positions are open"
    db.set_paused(name, paused)
    bus.emit("agent", {"name": name, "status": "paused" if paused else "running"})
    return True, f"{name} {'paused' if paused else 'resumed'}"


@app.post("/api/agents/{name}/{op}")
def agent_op(name: str, op: str) -> Response:
    if op not in ("pause", "resume"):
        return err("unknown op", 404)
    if name not in AGENTS:
        return err("unknown agent", 404)
    ok, msg = set_agent_paused(name, op == "pause")
    return JSONResponse({"ok": True, "name": name, "status": "paused" if op == "pause" else "running"}) \
        if ok else err(msg)


@app.post("/api/mode")
async def mode(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    ok, msg = report.switch_mode(str((body or {}).get("mode", "")), (body or {}).get("confirm"), "app")
    return JSONResponse({"ok": True, "mode": config.get("desk.mode"), "message": msg}) if ok else err(msg)


@app.post("/api/push")
async def push_sub(request: Request) -> Response:
    try:
        s = await request.json()
        endpoint, keys = s["endpoint"], s["keys"]
        p256dh, auth_ = keys["p256dh"], keys["auth"]
        assert str(endpoint).startswith("https://")
    except Exception:  # noqa: BLE001
        return err("subscription JSON {endpoint, keys:{p256dh, auth}}")
    db.x("INSERT INTO push_subs(endpoint,p256dh,auth,created_at) VALUES(?,?,?,?) ON CONFLICT(endpoint) "
         "DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth", (endpoint, p256dh, auth_, db.now()))
    return JSONResponse({"ok": True})


@app.post("/api/test-push")
async def test_push() -> dict:
    from . import notify
    await notify.push("Test push. The desk can reach you.", [("Open FOMO", links.signup_url())], kind="test")
    return {"ok": True}


@app.post("/api/actions/{op}/{id_}")
async def action(op: str, id_: int) -> Response:
    """Same callbacks as the Telegram buttons: bought/<signal_id>, sold/<position_id>, skip/<signal_id>."""
    if op not in ("bought", "sold", "skip"):
        return err("unknown action", 404)
    try:
        mod = importlib.import_module("desk.actions")
        res = await getattr(mod, op)(id_)
    except Exception as e:  # noqa: BLE001
        log.exception("action %s failed", op)
        return err(f"{op} failed: {type(e).__name__}", 500)
    return JSONResponse({"ok": True, "result": res if isinstance(res, (dict, str, type(None))) else str(res)})


@app.get("/api/search")
def search(q: str = "") -> dict:
    s = q.strip().lstrip("$")
    if not s:
        return {"rows": []}
    like = f"%{s}%"
    rows = db.q("SELECT mint, name, symbol, chain FROM coins WHERE mint=? OR symbol LIKE ? OR name LIKE ? "
                "ORDER BY (mint=?) DESC, (UPPER(symbol)=UPPER(?)) DESC, first_seen DESC LIMIT 20",
                (s, like, like, s, s))
    for r in rows:
        r["chain"] = r["chain"] or "solana"
    return {"rows": rows}


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    q = bus.hub.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"event: {ev['type']}\ndata: {json.dumps(ev['data'], default=str)}\n\n"
        finally:
            bus.hub.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/copy")
def copy_feed() -> dict:
    return copyfeed.feed()


@app.get("/api/replay")
def replay_view(h: float = 24) -> dict:
    return replay.summary(h)


def _post_json(p: dict | None) -> dict:
    if not p:
        return {"date": None, "text": None, "cardUrl": None}
    tok = os.environ.get("APP_TOKEN", "")
    name = f"{p['date']}-card.png" if not p.get("preview") else "preview-card.png"
    card = (report.REPORTS_DIR / name).is_file()
    return {"date": p["date"], "text": p["text"], "cardUrl": f"/reports/{name}?t={tok}&v={int(db.now())}" if card else None}


@app.get("/api/night-post")
def night_post() -> dict:
    return _post_json(nightpost.latest(report.REPORTS_DIR))


@app.post("/api/night-post/preview")
async def night_post_preview() -> dict:
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    p = await asyncio.to_thread(nightpost.build, report.night_start_ts(), date, report.REPORTS_DIR / "preview")
    import shutil
    src = report.REPORTS_DIR / "preview" / f"{date}-card.png"
    if src.is_file():
        shutil.copyfile(src, report.REPORTS_DIR / "preview-card.png")
    return _post_json({"date": date, "text": p["text"], "preview": True})


@app.get("/api/{rest:path}")
def api_404(rest: str) -> Response:
    return err("not found", 404)


# ---- reports + static --------------------------------------------------------------------
@app.get("/reports/{name}")
def report_file(name: str) -> Response:
    if not (name.endswith(".html") or name.endswith("-card.png")) or "/" in name or ".." in name:
        return err("not found", 404)
    p = report.REPORTS_DIR / name
    if not p.is_file():
        return err("not found", 404)
    return FileResponse(p, media_type="image/png" if name.endswith(".png") else "text/html", headers=NO_CACHE)


def _static(path: str) -> Response:
    root = WEB.resolve()
    p = (root / (path or "index.html")).resolve()
    if root not in p.parents and p != root:
        return Response("not found", status_code=404)
    if p.is_dir():
        p = p / "index.html"
    if not p.is_file():
        return Response("not found", status_code=404)
    headers = NO_CACHE if p.name in ("index.html", "app.js", "sw.js") else {"Cache-Control": "public, max-age=3600"}
    media = "application/manifest+json" if p.suffix == ".webmanifest" else None
    return FileResponse(p, headers=headers, media_type=media)


@app.get("/")
def index() -> Response:
    return _static("index.html")


@app.get("/{path:path}")
def static(path: str) -> Response:
    if path.startswith("._") or "/._" in path:
        return Response("not found", status_code=404)
    return _static(path)


# ---- price loop --------------------------------------------------------------------------
def _touched_tonight(since: float) -> list[tuple[str, str]]:
    """Mints touched tonight that nobody else is pricing (no price row in the last 90s,
    not an open position: Risk prices those)."""
    open_m = {p["mint"] for p in db.open_positions()}
    rows = db.q("SELECT DISTINCT a.mint, COALESCE(c.chain,'solana') AS chain FROM activity a "
                "LEFT JOIN coins c ON c.mint=a.mint WHERE a.ts>=? AND a.mint IS NOT NULL "
                "UNION SELECT DISTINCT s.mint, COALESCE(c.chain,'solana') FROM signals s "
                "LEFT JOIN coins c ON c.mint=s.mint WHERE s.ts>=? "
                "UNION SELECT DISTINCT p.mint, COALESCE(c.chain,'solana') FROM positions p "
                "LEFT JOIN coins c ON c.mint=p.mint WHERE p.closed_at>=?", (since, since, since))
    fresh = {r["mint"] for r in db.q("SELECT DISTINCT mint FROM prices WHERE ts>=?", (db.now() - 90,))}
    return [(r["mint"], r["chain"]) for r in rows if r["mint"] not in open_m and r["mint"] not in fresh]


async def _fetch_prices(mints: list[tuple[str, str]]) -> dict[str, tuple[float | None, float | None]]:
    """Reads through desk.market (DexScreener via desk.fields). Returns mint -> (price, volume_m5)."""
    try:
        market = importlib.import_module("desk.market")
    except ImportError:
        return {}
    by_chain: dict[str, list[str]] = {}
    for m, ch in mints:
        by_chain.setdefault(ch, []).append(m)
    out: dict[str, tuple[float | None, float | None]] = {}
    for ch, ms in by_chain.items():
        for i in range(0, len(ms), 30):
            res = await market.market_reads(ch, ms[i:i + 30])
            for m, v in res.items():
                out[m] = (_num(v.get("priceUsd")), _num(v.get("vol_m5")))
    return out


async def price_loop() -> None:
    while True:
        try:
            todo = _touched_tonight(report.night_start_ts())
            if todo:
                got = await _fetch_prices(todo[:60])
                for m, (p, v) in got.items():
                    if p is not None:
                        db.add_price(m, p, v)
                        bus.emit("price", {"mint": m, "price": p, "ts": db.now()})
        except Exception:
            log.exception("price loop failed")
        await asyncio.sleep(60)
