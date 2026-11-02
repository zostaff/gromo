"""Morning post: the night as a ready X post (English text + 1200x675 card), sent to Telegram at the
morning report. The desk never posts anywhere by itself: the owner copies it and posts by hand.

Every number comes from the desk db. No trades = the post says so.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from pathlib import Path

from . import config, db, replay

log = logging.getLogger("report")

ASSETS = Path(__file__).resolve().parent / "assets"
LIME, INK, WHITE, GREY = (205, 255, 0), (10, 10, 10), (245, 245, 247), (142, 142, 147)
GREEN, RED, ORANGE = (48, 209, 88), (255, 69, 58), (255, 159, 10)
AGENT = {"scanner": (100, 210, 255), "auditor": (10, 132, 255), "analyst": (255, 214, 10),
         "executor": (0, 211, 90), "risk": (255, 107, 154)}


# ---- numbers ---------------------------------------------------------------------------------
def _n(sql: str, args: tuple) -> int:
    r = db.q1(sql, args)
    return int((r or {}).get("n") or 0)


def stats(since: float, until: float | None = None) -> dict:
    until = until or db.now()
    a = (since, until)
    closed = db.q("SELECT mint, pnl_usd, entry_price, exit_price, exit_reason FROM positions "
                  "WHERE status='closed' AND closed_at>=? AND closed_at<?", a)
    night = db.current_night() or {}
    start = night.get("start_balance")
    pnl = round(sum(p["pnl_usd"] or 0 for p in closed), 2)
    best = max(closed, key=lambda p: p["pnl_usd"] or 0, default=None)
    rp = replay.summary(max(6.0, (until - since) / 3600))
    return {
        "mode": night.get("mode") or config.get("desk.mode"),
        "heard": _n("SELECT COUNT(*) AS n FROM coins WHERE first_seen>=? AND first_seen<?", a),
        "killed": _n("SELECT COUNT(DISTINCT mint) AS n FROM scans WHERE verdict='kill' AND ts>=? AND ts<?", a),
        "audited": _n("SELECT COUNT(DISTINCT mint) AS n FROM audits WHERE ts>=? AND ts<?", a),
        "auditor_no": _n("SELECT COUNT(DISTINCT mint) AS n FROM activity WHERE kind='rejected' AND agent='auditor' "
                         "AND ts>=? AND ts<?", a),
        "analyst_no": _n("SELECT COUNT(DISTINCT mint) AS n FROM activity WHERE kind='rejected' AND agent='analyst' "
                         "AND ts>=? AND ts<?", a),
        "buttons": _n("SELECT COUNT(*) AS n FROM signals WHERE status='sent' AND ts>=? AND ts<?", a),
        "trades": len(closed), "wins": sum(1 for p in closed if (p["pnl_usd"] or 0) >= 0),
        "pnl": pnl, "start": start, "balance": config.stack(),
        "pnl_pct": round(100 * pnl / start, 2) if start else None,
        "best": ({"symbol": (db.coin(best["mint"]) or {}).get("symbol") or best["mint"][:6],
                  "pct": round((best["exit_price"] / best["entry_price"] - 1) * 100, 1)
                  if best["exit_price"] and best["entry_price"] else None} if best and (best["pnl_usd"] or 0) > 0 else None),
        "refused": rp["summary"]["refused"], "dodged": rp["summary"]["dodged"], "dumped": rp["summary"]["dumped"],
        "replay_horizon_h": rp["horizonH"],
    }


def _money(v: float) -> str:
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def text(s: dict) -> str:
    """The X post. Plain English, hyphens only, every number from `s`."""
    lines = [f"Night report from my AI trading desk ({s['mode']}).", ""]
    lines.append(f"5 agents watched {s['heard']} new coins on Solana and Robinhood Chain.")
    lines.append(f"Scanner killed {s['killed']}. Auditor took {s['audited']} apart and refused {s['auditor_no']}. "
                 f"Analyst refused {s['analyst_no']}.")
    lines.append(f"{s['buttons']} made it to a Buy button.")
    lines.append("")
    if s["trades"]:
        pct = f" ({s['pnl_pct']:+.1f}% on the stack)" if s["pnl_pct"] is not None else ""
        lines.append(f"Closed {s['trades']} trade{'s' if s['trades'] != 1 else ''}, {s['wins']} green: "
                     f"{_money(s['pnl'])}{pct}.")
        if s["best"] and s["best"]["pct"] is not None:
            lines.append(f"Best: ${s['best']['symbol']} {s['best']['pct']:+.1f}%.")
    else:
        lines.append(f"No trades tonight. The stack stayed at ${s['balance']:,.2f}.")
    if s["refused"]:
        lines.append("")
        lines.append(f"Replay: of {s['refused']} coin{'s' if s['refused'] != 1 else ''} it said no to, {s['dodged']} would have lost money "
                     f"under its own stop rules within {s['replay_horizon_h']:g}h.")
        if s["dumped"]:
            lines.append(f"{s['dumped']} fell more than 50%.")
    lines += ["", "Built on GROKHOOD + FOMO."]
    return "\n".join(lines)


# ---- card ------------------------------------------------------------------------------------
def _font(name: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(ASSETS / "fonts" / name), size)


def card(s: dict, date_label: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter
    W, H = 1200, 675
    img = Image.new("RGB", (W, H), (0, 0, 0))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((700, 260, 1360, 900), fill=(60, 80, 0))
    gd.ellipse((-260, -320, 420, 260), fill=(18, 26, 0))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(120)), 1.0)
    d = ImageDraw.Draw(img)
    black, bold, med = (lambda z: _font("InterDisplay-Black.ttf", z)), (lambda z: _font("Inter-Bold.ttf", z)), \
        (lambda z: _font("Inter-Medium.ttf", z))

    # header
    head = f"NIGHT  ·  {date_label.upper()}"
    d.text((56, 48), head, font=bold(22), fill=LIME)
    hx = 56 + d.textlength(head, font=bold(22)) + 18
    mode = s["mode"]
    mcol = ORANGE if mode == "PAPER" else GREEN
    mw = d.textlength(mode, font=bold(17)) + 26
    d.rounded_rectangle((hx, 46, hx + mw, 76), radius=15, outline=mcol, width=2)
    d.text((hx + 13, 50), mode, font=bold(17), fill=mcol)
    wm = "GROKHOOD DESK"
    d.text((W - 56 - d.textlength(wm, font=black(26)), 44), wm, font=black(26), fill=WHITE)

    # big number: PnL if traded, else coins watched
    if s["trades"]:
        big, sub = _money(s["pnl"]), (f"{s['pnl_pct']:+.1f}% tonight" if s["pnl_pct"] is not None else "tonight")
        bcol = LIME if s["pnl"] >= 0 else RED
    else:
        big, sub, bcol = f"{s['heard']:,}", "coins watched, 0 trades", WHITE
    d.text((50, 96), big, font=black(150), fill=bcol)
    d.text((58, 274), sub, font=bold(34), fill=GREY)

    # funnel
    steps = [("scanner", "heard", s["heard"]), ("scanner", "killed", s["killed"]),
             ("auditor", "taken apart", s["audited"]), ("analyst", "refused", s["auditor_no"] + s["analyst_no"]),
             ("executor", "Buy buttons", s["buttons"])]
    x, y = 56, 352
    for ag, label, v in steps:
        val = f"{v:,}"
        w = max(d.textlength(val, font=black(46)), d.textlength(label, font=med(19))) + 40
        d.rounded_rectangle((x, y, x + w, y + 118), radius=24, fill=(22, 22, 24))
        d.ellipse((x + 20, y + 20, x + 32, y + 32), fill=AGENT[ag])
        d.text((x + 19, y + 38), val, font=black(46), fill=WHITE)
        d.text((x + 20, y + 88), label, font=med(19), fill=GREY)
        x += w + 12

    # replay band
    if s["refused"]:
        msg = f"Said no to {s['refused']}. {s['dodged']} would have lost money."
        d.rounded_rectangle((56, 500, 56 + d.textlength(msg, font=bold(30)) + 44, 560), radius=30, fill=LIME)
        d.text((78, 511), msg, font=bold(30), fill=INK)
        d.text((60, 576), f"Replay: same stop rules, {s['replay_horizon_h']:g}h after each decision",
               font=med(20), fill=GREY)
    d.text((56, H - 50), "5 AI agents  ·  Solana + Robinhood Chain  ·  built on GROKHOOD + FOMO",
           font=med(20), fill=(99, 99, 102))

    # mascot
    try:
        m = Image.open(ASSETS / "logo.png").convert("RGBA").resize((236, 236))
        mask = Image.new("L", m.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 235, 235), radius=58, fill=255)
        m.putalpha(mask)
        m = m.rotate(-8, resample=Image.BICUBIC, expand=True)
        img.paste(m, (W - 300, H - 330), m)
    except OSError:
        pass
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---- build / send ------------------------------------------------------------------------------
def build(since: float, date: str, out_dir: Path) -> dict:
    s = stats(since)
    label = datetime.strptime(date, "%Y-%m-%d").strftime("%b %-d")
    out_dir.mkdir(parents=True, exist_ok=True)
    body = text(s)
    png = None
    try:
        png = card(s, label)
        (out_dir / f"{date}-card.png").write_bytes(png)
    except Exception:  # noqa: BLE001 - a broken card must not block the text
        log.exception("night card render failed")
    (out_dir / f"{date}-post.json").write_text(json.dumps({"date": date, "text": body, "stats": s}), encoding="utf-8")
    return {"date": date, "text": body, "png": png}


def latest(out_dir: Path) -> dict | None:
    posts = sorted(out_dir.glob("*-post.json")) if out_dir.is_dir() else []
    posts = [p for p in posts if not p.name.startswith("preview")]
    if not posts:
        return None
    return json.loads(posts[-1].read_text(encoding="utf-8"))


async def send(post: dict, report_url: str | None) -> None:
    """Telegram: the card with the post as caption (fits 1024), else text. Web Push: a short line."""
    from . import notify
    chat = notify.owner_chat_id()
    buttons = [("Open report", report_url)] if report_url else []
    if chat is not None and post.get("png") and len(post["text"]) <= 1024:
        payload = {"chat_id": str(chat), "caption": post["text"]}
        kb = notify.keyboard(buttons)
        if kb:
            payload["reply_markup"] = json.dumps(kb)
        res = await notify.tg_call("sendPhoto", payload,
                                   files={"photo": (f"{post['date']}.png", post["png"], "image/png")}, timeout=40)
        if res is not None:
            await notify.webpush_all("Morning post is ready: card + text for X", None)
            return
    await notify.push("Morning post for X:\n\n" + post["text"], buttons, kind="report")
