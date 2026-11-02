"""Tracker: polls page 1 of swaps per followed trader, sends trade cards and breaking cards.

- first poll of a trader marks every existing swap as seen (no flood of old trades)
- new swaps are enriched with POST /proxy/filterTokens (batched, 60s cache) and, for Robinhood
  Chain coins, the GROKHOOD Safety Score (our edge)
- filters: min trade $, min mcap $, min liquidity $, networks, sells on/off, silent
  (an unknown value passes and shows "n/a": we never invent numbers)
- breaking: N followed traders buy the same coin within the window, 1 card per coin per window
- request budget: traders * (60 / interval) <= 120 per minute, else the interval is raised
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

from . import fields, links, store
from .fomo_api import FomoAPI, Unauthorized, set_token, token, token_age_s  # noqa: F401
from .i18n import t

log = logging.getLogger("copybot.tracker")

QUOTE_SYMBOLS = {"USDC", "USDT", "SOL", "WSOL", "WETH", "ETH", "USDC.E", "USDBC"}
QUOTE_ADDRS = {
    "so11111111111111111111111111111111111111112",          # wrapped SOL
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",          # USDC (Solana)
    "es9vmfrzacermjfrf4h2fyd4kconky11mcce8benwnyb",         # USDT (Solana)
    "0x4200000000000000000000000000000000000006",            # WETH (OP-stack chains)
    "0x0000000000000000000000000000000000000000",            # native ETH
}
MAX_REQ_PER_MIN = 120

Send = Callable[[str, list[tuple[str, str]], bool], Awaitable[Any]]


# ---- pure helpers (tested) ------------------------------------------------------------
def is_quote(symbol: Any, address: Any) -> bool:
    if symbol and str(symbol).upper() in QUOTE_SYMBOLS:
        return True
    return bool(address) and str(address).lower() in QUOTE_ADDRS


def infer_side(in_sym: Any, in_addr: Any, out_sym: Any, out_addr: Any) -> tuple[str, Any, Any]:
    """-> (side, coin_symbol, coin_address). Swap INTO USDC/USDT/SOL/WETH = sell of the other token."""
    if is_quote(out_sym, out_addr) and not is_quote(in_sym, in_addr):
        return "sell", in_sym, in_addr
    return "buy", out_sym, out_addr


def parse_ts(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) / 1000 if v > 1e12 else float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        n = fields.num(v)
        return (n / 1000 if n and n > 1e12 else n) if n is not None else None


def build_trade(swap: dict, trader: dict, tok: dict | None = None) -> dict | None:
    sid = fields.fomo(swap, "swap.id")
    if sid is None:
        return None
    in_sym, in_addr = fields.fomo(swap, "swap.in_symbol"), fields.fomo(swap, "swap.in_address")
    out_sym, out_addr = fields.fomo(swap, "swap.out_symbol"), fields.fomo(swap, "swap.out_address")
    side, sym, addr = infer_side(in_sym, in_addr, out_sym, out_addr)
    explicit = str(fields.fomo(swap, "swap.side") or "").lower()
    if explicit in ("buy", "sell"):
        side = explicit
    cid = fields.fomo(swap, "swap.chain_id")
    if cid is None and tok:
        cid = fields.fomo(tok, "token.chain_id")
    tok = tok or {}
    return {"swap_id": str(sid), "trader_id": str(trader["id"]), "handle": trader.get("handle") or "",
            "ts": parse_ts(fields.fomo(swap, "swap.ts")) or time.time(), "side": side,
            "address": str(addr) if addr else None, "chain_id": cid, "chain": links.chain_of(cid),
            "symbol": fields.fomo(tok, "token.symbol") or sym, "name": fields.fomo(tok, "token.name"),
            "usd": fields.num(fields.fomo(swap, "swap.usd")), "mcap": fields.num(fields.fomo(tok, "token.mcap")),
            "liq": fields.num(fields.fomo(tok, "token.liquidity")), "image": fields.fomo(tok, "token.image")}


def passes(tr: dict, f: dict) -> bool:
    if tr["side"] == "sell" and not f.get("sells", True):
        return False
    if tr["chain"] and tr["chain"] not in (f.get("networks") or []):
        return False
    for key, fk in (("usd", "min_trade_usd"), ("mcap", "min_mcap_usd"), ("liq", "min_liq_usd")):
        if tr[key] is not None and tr[key] < float(f.get(fk) or 0):
            return False
    return True


def money(v: float | None) -> str:
    if v is None:
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"${v / div:.1f}{suf}"
    return f"${v:,.0f}"


def coin_target(tr: dict) -> str:
    """URL for a coin: links.coin_url, or the FOMO signup link when the chain is unknown."""
    if tr.get("chain") and tr.get("address"):
        return links.coin_url(tr["address"], tr["chain"])
    return links.signup_url()


def a(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}">{html.escape(text, quote=False)}</a>'


def gh_line(gh: dict | None, lang: str = "en") -> str | None:
    """GROKHOOD Safety Score line, or a warning line when the coin is risky."""
    if not isinstance(gh, dict):
        return None
    if fields.grokhood(gh, "ring") or fields.grokhood(gh, "operator"):
        return t("rug_ring", lang)
    if fields.grokhood(gh, "verdict_level") == "danger":
        return t("gh_warn", lang, label=fields.grokhood(gh, "verdict_label") or "danger")
    score = fields.num(fields.grokhood(gh, "score"))
    return f"GROKHOOD {int(score)}/100" if score is not None else None


def trade_card(tr: dict, lang: str = "en", gh: dict | None = None) -> tuple[str, list[tuple[str, str]]]:
    sym = tr.get("symbol") or (tr.get("address") or "?")[:6]
    target = coin_target(tr)
    who = a(links.profile_url(tr["handle"]), "@" + tr["handle"].lstrip("@")) if tr["handle"] else "?"
    verb = t("bought" if tr["side"] == "buy" else "sold", lang)
    chain = links.CHAIN_LABEL.get(tr["chain"]) if tr.get("chain") else t("unknown_chain", lang)
    text = (f"{who} {verb} {a(target, sym)} for {money(tr['usd'])} · mcap {money(tr['mcap'])} · "
            f"liq {money(tr['liq'])} · {html.escape(chain)}")
    line = gh_line(gh, lang) if tr.get("chain") == "robinhood" else None
    if line:
        text += "\n" + html.escape(line)
    if tr.get("chain") and tr.get("address"):
        btn = (t("copy", lang, s=sym), target)
    else:
        btn = (t("open_fomo", lang), links.signup_url())
    return text, [btn]


class Breaking:
    """N distinct followed traders buy the same coin within window_s -> one alert per coin per window."""

    def __init__(self, alerted: dict[str, float] | None = None) -> None:
        self.buys: dict[str, list[tuple[float, str, str]]] = {}
        self.alerted: dict[str, float] = dict(alerted or {})

    def add(self, tr: dict, n: int, window_s: float, now: float | None = None) -> dict | None:
        if tr["side"] != "buy" or not tr.get("address"):
            return None
        now = now or tr["ts"]
        key = f"{tr.get('chain_id')}:{str(tr['address']).lower()}"
        rows = [r for r in self.buys.get(key, []) if now - r[0] <= window_s and r[1] != tr["trader_id"]]
        rows.append((tr["ts"], tr["trader_id"], tr["handle"]))
        self.buys[key] = rows
        if len({r[1] for r in rows}) < n:
            return None
        last = self.alerted.get(key)
        if last is not None and now - last < window_s:
            return None
        self.alerted[key] = now
        return {"key": key, "trade": tr, "handles": [r[2] for r in rows], "n": len({r[1] for r in rows}),
                "window_min": int(window_s // 60)}


def breaking_card(al: dict, lang: str = "en") -> tuple[str, list[tuple[str, str]]]:
    tr = al["trade"]
    sym = tr.get("symbol") or (tr.get("address") or "?")[:6]
    target = coin_target(tr)
    who = ", ".join(a(links.profile_url(h), "@" + h.lstrip("@")) for h in al["handles"] if h)
    text = t("breaking", lang, n=al["n"], coin=a(target, sym), w=al["window_min"], who=who)
    if tr.get("chain") and tr.get("address"):
        return text, [(t("copy", lang, s=sym), target)]
    return text, [(t("open_fomo", lang), links.signup_url())]


def effective_interval(n_traders: int, interval_s: float) -> float:
    """Raise the interval so traders * (60 / interval) stays within 120 requests per minute."""
    if n_traders <= 0:
        return float(interval_s)
    return float(max(interval_s, math.ceil(n_traders * 60 / MAX_REQ_PER_MIN)))


# ---- the loop ------------------------------------------------------------------------
class Tracker:
    def __init__(self, api: FomoAPI, send: Send, ask_token: Callable[[str], Awaitable[Any]],
                 lang: Callable[[], str]) -> None:
        self.api = api
        self.send = send
        self.ask_token = ask_token
        self.lang = lang
        self.breaking = Breaking({r["coin"]: r["ts"] for r in store.q("SELECT coin, ts FROM breaking")})
        self.errors = 0
        self.last_poll: float | None = None
        self.wake = asyncio.Event()

    def state(self) -> str:
        if store.kv_get("paused", False):
            return "paused"
        if store.kv_get("need_token", False) or not token():
            return "need_token"
        return "running"

    async def poll_once(self) -> int:
        f = store.filters()
        lang = self.lang()
        new: list[tuple[dict, dict]] = []
        for tr in store.traders():
            swaps = await self.api.swaps(tr["id"])
            ids = [str(i) for i in (fields.fomo(s, "swap.id") for s in swaps) if i is not None]
            if not tr["initialized"]:
                store.mark_seen(ids, tr["id"])
                store.x("UPDATE traders SET initialized=1, last_poll=? WHERE id=?", (time.time(), tr["id"]))
                continue
            fresh = [s for s in swaps if fields.fomo(s, "swap.id") is not None
                     and not store.is_seen(str(fields.fomo(s, "swap.id")))]
            store.mark_seen([str(fields.fomo(s, "swap.id")) for s in fresh], tr["id"])
            store.x("UPDATE traders SET last_poll=? WHERE id=?", (time.time(), tr["id"]))
            new += [(s, tr) for s in fresh]
        if not new:
            return 0
        trades = [x for x in (build_trade(s, tr) for s, tr in new) if x]
        keys = [f"{x['address']}:{x['chain_id']}" for x in trades if x["address"] and x["chain_id"] is not None]
        toks = await self.api.filter_tokens(keys) if keys else {}
        sent = 0
        for (s, tr), x in zip(new, [build_trade(s, tr) for s, tr in new]):
            if x is None:
                continue
            tok = toks.get(f"{x['address']}:{x['chain_id']}")
            if tok:
                x = build_trade(s, tr, tok) or x
            gh = await self.api.grokhood_token(x["address"]) if x["chain"] == "robinhood" and x["address"] else None
            ok = passes(x, f)
            store.save_trade(x, ok)
            if ok:
                text, btns = trade_card(x, lang, gh)
                await self.send(text, btns, bool(f.get("silent")))
                sent += 1
            al = self.breaking.add(x, int(f["breaking_n"]), float(f["breaking_window_min"]) * 60, time.time())
            if al and (not x["chain"] or x["chain"] in (f.get("networks") or [])):
                store.x("INSERT INTO breaking(coin,ts) VALUES(?,?) ON CONFLICT(coin) DO UPDATE SET ts=excluded.ts",
                        (al["key"], time.time()))
                text, btns = breaking_card(al, lang)
                if x["chain"] == "robinhood":
                    line = gh_line(gh, lang)
                    if line:
                        text += "\n" + html.escape(line)
                await self.send(text, btns, False)
        return sent

    async def _token_watch(self) -> None:
        age = token_age_s()
        if age is not None and age >= 55 * 60 and not store.kv_get("token_warned", False):
            store.kv_set("token_warned", True)
            await self.ask_token(t("token_warn", self.lang()))

    async def run(self) -> None:
        while True:
            f = store.filters()
            interval = effective_interval(len(store.traders()), float(f["interval_s"]))
            try:
                st = self.state()
                if st == "need_token":
                    if not store.kv_get("token_asked", False):
                        store.kv_set("token_asked", True)
                        await self.ask_token(t("ask_token", self.lang()))
                elif st == "running":
                    await self._token_watch()
                    await self.poll_once()
                    self.last_poll = time.time()
                    self.errors = 0
            except Unauthorized:
                log.warning("FOMO 401: tracker paused until a new token")
                store.kv_set("need_token", True)
                store.kv_set("token_asked", True)
                await self.ask_token(t("ask_token", self.lang()))
            except Exception as e:  # noqa: BLE001
                self.errors += 1
                log.warning("poll failed: %s", type(e).__name__)
                interval = min(interval * (2 ** min(self.errors, 4)), 600)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
