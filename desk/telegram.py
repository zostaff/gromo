"""Telegram long polling: owner lock, callback buttons, commands.

Owner lock: the first /start in a private chat becomes kv owner_chat_id. Everyone else is ignored
silently. Commands work as /cmd or plain text. Every coin mentioned carries its links.coin_url.
"""
from __future__ import annotations

import asyncio
import html
import importlib
import inspect
import logging
import os
import re
from datetime import datetime
from typing import Any

from . import config, db, links, notify, report

log = logging.getLogger("telegram")

AGENTS = report.AGENTS
HELP = """Commands (with or without /):
buy X - Buy on FOMO button for a coin
sell X - Sell on FOMO button for an open position
why X - which agent stopped X and why
pause / resume <agent> - scanner, auditor, analyst, executor, risk
set <setting> to <value> - e.g. set stop to 8
stack N - set the stack in $
status - balance, agents, open positions
paper | live - switch mode (live needs typing LIVE)
invite - your FOMO signup link
report - tonight's report so far
X = name, symbol or address."""


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else "n/a"), quote=False)


def coin_a(c: dict) -> str:
    label = c.get("symbol") or c.get("name") or c["mint"][:6] + ".."
    return f'<a href="{html.escape(links.coin_url(c["mint"], c.get("chain") or "solana"))}">{esc(label)}</a>'


def hm(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M") if ts else "n/a"


async def reply(chat: int, text_html: str, buttons: list[tuple[str, str]] | None = None) -> None:
    await notify.tg_send(chat, text_html, buttons, is_html=True)


# ---- coin resolution ------------------------------------------------------------------------
def guess_chain(addr: str) -> str | None:
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
        return "robinhood"
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr):
        return "solana"
    return None


def find_coins(x: str) -> list[dict]:
    s = x.strip().lstrip("$")
    if not s:
        return []
    exact = db.q("SELECT * FROM coins WHERE mint=? OR LOWER(mint)=LOWER(?)", (s, s))
    if exact:
        return exact[:1]
    rows = db.q("SELECT * FROM coins WHERE UPPER(symbol)=UPPER(?) OR UPPER(name)=UPPER(?) "
                "ORDER BY first_seen DESC LIMIT 10", (s, s))
    if rows:
        return rows
    return db.q("SELECT * FROM coins WHERE symbol LIKE ? OR name LIKE ? ORDER BY first_seen DESC LIMIT 10",
                (f"%{s}%", f"%{s}%"))


async def resolve_one(chat: int, x: str) -> dict | None:
    rows = find_coins(x)
    if len(rows) == 1:
        return rows[0]
    if not rows:
        ch = guess_chain(x.strip())
        if ch:
            return {"mint": x.strip(), "chain": ch, "name": None, "symbol": None}
        await reply(chat, f"No coin matches {esc(x)}. Use the symbol, name or address.")
        return None
    lines = "\n".join(f"{coin_a(c)} ({esc(links.CHAIN_LABEL.get(c.get('chain') or 'solana'))}) "
                      f"<code>{esc(c['mint'])}</code>" for c in rows)
    await reply(chat, f"Which one?\n{lines}\nReply with the same command and the address.")
    return None


# ---- commands -----------------------------------------------------------------------------
async def _maybe_await(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


async def cmd_buy(chat: int, arg: str) -> None:
    c = await resolve_one(chat, arg)
    if not c:
        return
    mint, chain = c["mint"], c.get("chain") or "solana"
    url = links.coin_url(mint, chain)
    pushed = db.kv_get(f"push:{mint}")
    if isinstance(pushed, dict) and float(pushed.get("ts") or 0) >= report.night_start_ts():
        btns = [tuple(b) for b in pushed.get("buttons") or []]
        if not any(l.startswith("Buy") for l, _ in btns):
            btns.insert(0, ("Buy on FOMO", url))
        await notify.tg_send(chat, pushed.get("text") or "", btns, image=(db.coin(mint) or {}).get("image"))
        return
    await reply(chat, f"Taking {coin_a(c)} apart...")
    res = None
    try:
        aud = importlib.import_module("desk.agents.auditor")
        fn = getattr(aud, "audit_now")
        try:
            res = await _maybe_await(fn(mint, chain))
        except TypeError:
            res = await _maybe_await(fn(mint))
    except Exception as e:  # noqa: BLE001
        log.exception("audit_now failed")
        await reply(chat, f"Audit failed for {coin_a(c)}: {esc(type(e).__name__)}", [("Buy on FOMO", url)])
        return
    layers = (res or {}).get("layers") or []
    mark = {True: "pass", False: "FAIL", None: "not checked"}
    lines = "\n".join(f"{l.get('n')}. {esc(l.get('name'))}: {mark.get(l.get('pass'), 'not checked')}, "
                      f"{esc(l.get('value'))}" for l in layers) or "no layers came back"
    c2 = db.coin(mint) or c
    verdict = "passed" if (res or {}).get("passed") else f"refused: {esc((res or {}).get('reason'))}"
    await reply(chat, f"{coin_a(c2)} {verdict}\n{lines}\n\nNot sized, not on the desk.", [("Buy on FOMO", url)])


async def cmd_sell(chat: int, arg: str) -> None:
    c = await resolve_one(chat, arg)
    if not c:
        return
    url = links.coin_url(c["mint"], c.get("chain") or "solana")
    p = db.q1("SELECT * FROM positions WHERE mint=? AND status IN ('open','exiting') ORDER BY opened_at DESC "
              "LIMIT 1", (c["mint"],))
    if not p:
        await reply(chat, f"No open position in {coin_a(c)}.", [("Open on FOMO", url)])
        return
    await reply(chat, f"Sell {coin_a(c)}: ${p['size_usd']:,.2f} in, entry {p['entry_price']:.8g}. "
                      f"Tap Sell on FOMO, then Sold.",
                [("Sell on FOMO", url), ("Sold", f"cb:sold:{p['id']}")])


def why_text(c: dict) -> str:
    mint = c["mint"]
    found: list[tuple[float, str]] = []
    s = db.q1("SELECT * FROM scans WHERE mint=? AND verdict='kill' ORDER BY ts DESC LIMIT 1", (mint,))
    if s:
        found.append((s["ts"], f"Scanner stopped it: {esc(s['reason'])} (liquidity "
                               f"{'$' + format(s['liquidity_usd'], ',.0f') if s['liquidity_usd'] else 'n/a'}, "
                               f"top 10 {esc(s['top10_pct'])}%, 1h {esc(s['h1_change_pct'])}%) at {hm(s['ts'])}"))
    a = db.q1("SELECT * FROM audits WHERE mint=? AND pass=0 ORDER BY ts DESC LIMIT 1", (mint,))
    if a:
        found.append((a["ts"], f"Auditor stopped it: layer {a['layer']} {esc(a['name'])}, value "
                               f"{esc(a['value_text'])} ({esc(a['source'])}) at {hm(a['ts'])}"))
    v = db.q1("SELECT * FROM verdicts WHERE mint=? AND pass=0 ORDER BY ts DESC LIMIT 1", (mint,))
    if v:
        found.append((v["ts"], f"Analyst stopped it: {esc(v['reason'])}, score {esc(v['score'])} "
                               f"({esc(v['model'])}) at {hm(v['ts'])}"))
    g = db.q1("SELECT * FROM signals WHERE mint=? AND status NOT IN ('bought','open','filled') "
              "ORDER BY ts DESC LIMIT 1", (mint,))
    if g and g["status"]:
        found.append((g["ts"], f"Executor: signal {esc(g['status'])}, {esc(g['reason'])} at {hm(g['ts'])}"))
    k = db.q1("SELECT * FROM skips WHERE mint=?", (mint,))
    if k:
        found.append((db.now(), f"You skipped it (until {hm(k['until'])})"))
    r = db.q1("SELECT * FROM activity WHERE mint=? AND kind='rejected' ORDER BY ts DESC LIMIT 1", (mint,))
    if r and not found:
        found.append((r["ts"], f"{esc(r['subtitle'])} at {hm(r['ts'])}"))
    if not found:
        return f"Nothing on the desk stopped {coin_a(c)}."
    found.sort(key=lambda t: -t[0])
    return f"{coin_a(c)}\n" + "\n".join(t for _, t in found[:3])


def fmt_setting(key: str, v: Any) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    if key == "risk.stop_pct":
        return f"-{v:g}%"
    if key.endswith("_pct") and isinstance(v, (int, float)):
        return f"{v:g}%"
    if key.endswith("_usd") or key in ("desk.live_stack", "desk.paper_start_stack"):
        return f"${v:,.2f}"
    return str(v)


async def cmd_set(chat: int, arg: str) -> None:
    m = re.match(r"(.+?)\s+to\s+(.+)$", arg, re.I) or re.match(r"(.+)\s+(\S+)$", arg)
    if not m:
        await reply(chat, "Use: set stop to 8")
        return
    name, value = m.group(1).strip(), m.group(2).strip()
    from .api import apply_setting
    try:
        k, old, new = await apply_setting(name, value, "telegram")
    except config.SettingError as e:
        await reply(chat, esc(e))
        return
    short = next((a for a, kk in config.ALIASES.items() if kk == k), k.split(".", 1)[1].replace("_", " "))
    await reply(chat, f"{esc(short)} {esc(fmt_setting(k, old))} -> {esc(fmt_setting(k, new))}")


async def cmd_stack(chat: int, arg: str) -> None:
    key = "desk.live_stack" if config.get("desk.mode") == "LIVE" else "desk.paper_start_stack"
    from .api import apply_setting
    try:
        k, old, new = await apply_setting(key, arg, "telegram")
    except config.SettingError as e:
        await reply(chat, esc(e))
        return
    tc = config.ticket_check(config.stack())
    extra = f"\n{esc(tc['message'])}" if tc["message"] else ""
    await reply(chat, f"stack {fmt_setting(k, old)} -> {fmt_setting(k, new)}{extra}")


def status_text() -> str:
    from .api import state
    s = state()
    lines = [f"{esc(s['mode'])} · ${s['balance']:,.2f} ({s['todayPct']:+.2f}% tonight)"]
    for a in s["agents"]:
        lines.append(f"{a['name'].capitalize()}: {a['status']}, {esc(a['subtitle'])}")
    ops = db.open_positions()
    if ops:
        lines.append("Open:")
        for p in ops:
            c = db.coin(p["mint"]) or {"mint": p["mint"]}
            last = p.get("last_price") or p["entry_price"]
            lines.append(f"{coin_a(c)} ${p['size_usd']:,.2f}, {(last / p['entry_price'] - 1) * 100:+.1f}%")
    return "\n".join(lines)


async def cmd_mode(chat: int, word: str) -> None:
    if word == "paper":
        ok, msg = report.switch_mode("PAPER", None, "telegram")
        await reply(chat, esc(msg))
        return
    if word == "LIVE":
        if not db.kv_get("live_pending") or db.now() - float(db.kv_get("live_pending")) > 300:
            await reply(chat, "Send live first, then LIVE within 5 minutes.")
            return
        db.kv_set("live_pending", None)
        ok, msg = report.switch_mode("LIVE", "LIVE", "telegram")
        await reply(chat, esc(msg))
        return
    if report.full_paper_nights() < 1:
        await reply(chat, "LIVE needs at least 1 full PAPER night first.")
        return
    db.kv_set("live_pending", db.now())
    await reply(chat, "LIVE uses your real stack setting. The desk still never places orders. "
                      "Type LIVE to confirm.")


async def handle_text(chat: int, text: str) -> None:
    t = text.strip()
    if t == "LIVE":
        await cmd_mode(chat, "LIVE")
        return
    t = t.lstrip("/")
    word, _, arg = t.partition(" ")
    word = word.split("@", 1)[0].lower()
    arg = arg.strip()
    if word in ("start", "help"):
        await reply(chat, esc(HELP))
    elif word == "buy" and arg:
        await cmd_buy(chat, arg)
    elif word == "sell" and arg:
        await cmd_sell(chat, arg)
    elif word == "why" and arg:
        c = await resolve_one(chat, arg)
        if c:
            await reply(chat, why_text(c), [("Open on FOMO", links.coin_url(c["mint"], c.get("chain") or "solana"))])
    elif word in ("pause", "resume") and arg:
        from .api import set_agent_paused
        ok, msg = set_agent_paused(arg.lower(), word == "pause")
        await reply(chat, esc(msg))
    elif word == "set" and arg:
        await cmd_set(chat, arg)
    elif word == "stack" and arg:
        await cmd_stack(chat, arg)
    elif word == "status":
        await reply(chat, status_text())
    elif word in ("paper", "live"):
        await cmd_mode(chat, word)
    elif word == "invite":
        u = links.signup_url()
        await reply(chat, f"New to FOMO? {esc(u)}", [("Join FOMO", u)])
    elif word == "report":
        date, _ = report.write_report(end=False)
        await reply(chat, f"Report so far for tonight ({date}).", [("Open report", report.report_url(date))])
    else:
        await reply(chat, esc(HELP))


async def handle_callback(cq: dict) -> None:
    data = str(cq.get("data") or "")
    m = re.fullmatch(r"cb:(bought|sold|skip):(\d+)", data)
    text = "Unknown button"
    if m:
        try:
            mod = importlib.import_module("desk.actions")
            res = await _maybe_await(getattr(mod, m.group(1))(int(m.group(2))))
            text = res if isinstance(res, str) else (res or {}).get("message", "Done") if isinstance(res, dict) \
                else "Done"
        except Exception as e:  # noqa: BLE001
            log.exception("callback %s failed", data)
            text = f"Failed: {type(e).__name__}"
    await notify.tg_call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": str(text)[:190]})


async def handle_update(u: dict) -> None:
    owner = notify.owner_chat_id()
    if "callback_query" in u:
        cq = u["callback_query"]
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
        if owner is None or chat != owner:
            return
        await handle_callback(cq)
        return
    msg = u.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    text = msg.get("text") or ""
    if chat is None or not text:
        return
    if owner is None:
        if text.strip().split("@")[0] == "/start" and (msg.get("chat") or {}).get("type") == "private":
            db.kv_set("owner_chat_id", chat)
            log.info("owner locked")
            await reply(chat, "This desk is yours now. Nobody else can talk to it.\n\n" + esc(HELP))
        return
    if chat != owner:
        return
    await handle_text(chat, text)


async def poll_loop() -> None:
    if not os.environ.get("TG_TOKEN"):
        log.info("TG_TOKEN not set, telegram off")
        while True:
            await asyncio.sleep(3600)
    offset = int(db.kv_get("tg_offset", 0) or 0)
    while True:
        res = await notify.tg_call("getUpdates", {"offset": offset, "timeout": 50,
                                                  "allowed_updates": ["message", "callback_query"]},
                                   timeout=65, retries=1)
        if res is None:
            await asyncio.sleep(5)
            continue
        for u in res:
            offset = max(offset, int(u["update_id"]) + 1)
            db.kv_set("tg_offset", offset)
            try:
                await handle_update(u)
            except Exception:
                log.exception("update failed")
