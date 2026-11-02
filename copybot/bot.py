"""FOMO copy-trade Telegram bot: `python -m copybot.bot`.

Separate process and separate bot token (COPYBOT_TG_TOKEN, a different bot than the desk's TG_TOKEN,
since two processes cannot long-poll one bot). Owner lock: the first /start in a private chat owns it.
Reads FOMO with FOMO_TOKEN of a SEPARATE EMPTY FOMO account. Never places orders.
One menu message, edited in place by inline buttons.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import sys
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any

import httpx

from . import fields, links, store
from .fomo_api import FomoAPI, Unauthorized, set_token, token, token_age_s
from .i18n import t
from .tracker import Tracker, a, money

log = logging.getLogger("copybot")

TG = "https://api.telegram.org"
PAGE = 10
CYCLES = {
    "min_trade_usd": [0, 50, 100, 250, 500, 1000, 5000],
    "min_mcap_usd": [0, 50_000, 100_000, 250_000, 500_000, 1_000_000, 5_000_000],
    "min_liq_usd": [0, 20_000, 50_000, 100_000, 250_000],
    "breaking_n": [2, 3, 4, 5],
    "breaking_window_min": [15, 30, 60, 120],
    "interval_s": [15, 30, 60, 120, 300],
}


class Bot:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(65, connect=10))
        self.api = FomoAPI()
        self.tracker = Tracker(self.api, self.send_card, self.notify_owner, self.lang)

    # ---- telegram plumbing -------------------------------------------------------------
    async def tg(self, method: str, payload: dict, timeout: float = 20, retries: int = 3) -> Any:
        tok = os.environ.get("COPYBOT_TG_TOKEN", "").strip()
        if not tok:
            return None
        last = ""
        for i in range(retries):
            try:
                r = await self.http.post(f"{TG}/bot{tok}/{method}", json=payload, timeout=timeout)
                body = r.json()
                if body.get("ok"):
                    return body.get("result")
                last = f"{r.status_code} {body.get('description', '')}"
                if r.status_code == 429:
                    await asyncio.sleep(min(float((body.get("parameters") or {}).get("retry_after", 2)), 30))
                    continue
                if 400 <= r.status_code < 500:
                    break
            except (httpx.HTTPError, ValueError) as e:
                last = type(e).__name__
            await asyncio.sleep(1.5 * (i + 1))
        if "message is not modified" not in last:
            log.warning("telegram %s failed: %s", method, last)
        return None

    def owner(self) -> int | None:
        v = store.kv_get("owner_chat_id")
        return int(v) if v is not None else None

    def lang(self) -> str:
        return store.kv_get("lang", "en") or "en"

    @staticmethod
    def kb(rows: list[list[tuple[str, str]]]) -> dict:
        return {"inline_keyboard": [[{"text": l, "url": d} if d.startswith("http") else
                                     {"text": l, "callback_data": d[:64]} for l, d in row] for row in rows if row]}

    async def say(self, chat: int, text: str, rows: list[list[tuple[str, str]]] | None = None,
                  silent: bool = False) -> Any:
        p: dict[str, Any] = {"chat_id": chat, "text": text[:4096], "parse_mode": "HTML",
                             "disable_web_page_preview": True, "disable_notification": silent}
        if rows:
            p["reply_markup"] = self.kb(rows)
        return await self.tg("sendMessage", p)

    async def send_card(self, text: str, buttons: list[tuple[str, str]], silent: bool) -> None:
        chat = self.owner()
        if chat is not None:
            await self.say(chat, text, [buttons], silent=silent)

    async def notify_owner(self, text: str) -> None:
        chat = self.owner()
        if chat is not None:
            await self.say(chat, html.escape(text, quote=False))

    async def show(self, chat: int, text: str, rows: list[list[tuple[str, str]]], msg_id: int | None = None) -> None:
        """Edit the one menu message, or send a new one."""
        mid = None if msg_id == -1 else (msg_id or store.kv_get("menu_msg"))
        if mid:
            res = await self.tg("editMessageText", {"chat_id": chat, "message_id": mid, "text": text[:4096],
                                                    "parse_mode": "HTML", "disable_web_page_preview": True,
                                                    "reply_markup": self.kb(rows)})
            if res is not None:
                return
        res = await self.say(chat, text, rows)
        if isinstance(res, dict):
            store.kv_set("menu_msg", res.get("message_id"))

    # ---- views -----------------------------------------------------------------------------
    def state_label(self) -> str:
        return t(self.tracker.state(), self.lang())

    def main_view(self) -> tuple[str, list]:
        L = self.lang()
        paused = store.kv_get("paused", False)
        text = t("menu", L, n=len(store.traders()), state=self.state_label())
        return html.escape(text, quote=False), [
            [(t("accounts", L), "m:acc:0"), (t("filters", L), "m:flt")],
            [(t("recent", L), "m:rec"), (t("status", L), "m:st")],
            [(t("resume" if paused else "pause", L), "m:pause"), (t("language", L), "m:lang")]]

    def accounts_view(self, page: int) -> tuple[str, list]:
        L = self.lang()
        tr = store.traders()
        pages = max(1, (len(tr) + PAGE - 1) // PAGE)
        page = max(0, min(page, pages - 1))
        rows = [[("@" + (x["handle"] or x["id"]), f"m:prof:{x['id']}")] for x in tr[page * PAGE:(page + 1) * PAGE]]
        nav = []
        if page > 0:
            nav.append(("<", f"m:acc:{page - 1}"))
        if page < pages - 1:
            nav.append((">", f"m:acc:{page + 1}"))
        rows += [nav, [(t("add", L), "m:add"), (t("search", L), "m:search")],
                 [(t("import", L), "m:imp"), (t("delete_all", L), "m:delall")], [(t("back", L), "m:main")]]
        return html.escape(t("accounts_title", L, n=len(tr), p=page + 1, pages=pages), quote=False), rows

    async def profile_view(self, tid: str) -> tuple[str, list]:
        L = self.lang()
        x = store.q1("SELECT * FROM traders WHERE id=?", (tid,))
        if not x:
            return self.accounts_view(0)
        url = links.profile_url(x["handle"] or "")
        n = store.q1("SELECT COUNT(*) AS c FROM trades WHERE trader_id=?", (tid,))["c"]
        holdings = "n/a"
        try:
            bals = await self.api.balances(tid)
            usd = [fields.num(fields.fomo(b, "balance.usd")) for b in bals]
            known = [u for u in usd if u is not None]
            holdings = f"{len(bals)} tokens" + (f", {money(sum(known))}" if known else "")
        except Unauthorized:
            holdings = t("need_token", L)
        except Exception:  # noqa: BLE001
            pass
        text = (f"{a(url, '@' + (x['handle'] or ''))}\n{html.escape(x['name'] or '')}\n"
                f"Holdings: {html.escape(holdings)}\nTrades seen: {n}")
        return text, [[(t("profile", L), url)], [(t("delete", L), f"m:del:{tid}")], [(t("back", L), "m:acc:0")]]

    def filters_view(self) -> tuple[str, list]:
        L = self.lang()
        f = store.filters()
        onoff = lambda v: t("on" if v else "off", L)  # noqa: E731
        nets = [(("[x] " if c in f["networks"] else "[ ] ") + links.CHAIN_LABEL[c], f"m:net:{c}")
                for c in links.CHAIN_ID]
        rows = [[(t("f_min_trade", L, v=f"{f['min_trade_usd']:,}"), "m:f:min_trade_usd")],
                [(t("f_min_mcap", L, v=f"{f['min_mcap_usd']:,}"), "m:f:min_mcap_usd")],
                [(t("f_min_liq", L, v=f"{f['min_liq_usd']:,}"), "m:f:min_liq_usd")],
                nets[:2], nets[2:],
                [(t("f_sells", L, v=onoff(f["sells"])), "m:f:sells"), (t("f_silent", L, v=onoff(f["silent"])), "m:f:silent")],
                [(t("f_breaking", L, n=f["breaking_n"], w=f["breaking_window_min"]), "m:f:breaking_n")],
                [(f"{f['breaking_window_min']} min", "m:f:breaking_window_min"),
                 (t("f_interval", L, v=f["interval_s"]), "m:f:interval_s")],
                [(t("back", L), "m:main")]]
        return html.escape(t("filters_title", L), quote=False), rows

    def recent_view(self, w: str | None) -> tuple[str, list]:
        L = self.lang()
        rows = [[("1h", "m:rec:1h"), ("6h", "m:rec:6h"), ("24h", "m:rec:24h")], [(t("back", L), "m:main")]]
        if not w:
            return html.escape(t("recent", L), quote=False), rows
        secs = {"1h": 3600, "6h": 6 * 3600, "24h": 86400}[w]
        lines = []
        for x in store.recent_trades(time.time() - secs, 25):
            sym = x["symbol"] or (x["address"] or "?")[:6]
            target = links.coin_url(x["address"], x["chain"]) if x["chain"] and x["address"] else links.signup_url()
            when = datetime.fromtimestamp(x["ts"]).strftime("%H:%M")
            lines.append(f"{when} {a(links.profile_url(x['handle'] or ''), '@' + (x['handle'] or ''))} "
                         f"{t('bought' if x['side'] == 'buy' else 'sold', L)} {a(target, sym)} {money(x['usd'])}")
        body = "\n".join(lines) or t("none", L)
        return html.escape(t("recent_title", L, w=w), quote=False) + "\n" + body, rows

    def status_view(self) -> tuple[str, list]:
        L = self.lang()
        from .tracker import effective_interval
        f = store.filters()
        age = token_age_s()
        tok = t("token_none", L) if not token() else (t("token_age", L, m=int(age // 60)) if age is not None else "env")
        last = datetime.fromtimestamp(self.tracker.last_poll).strftime("%H:%M:%S") if self.tracker.last_poll else "n/a"
        text = t("status_text", L, state=self.state_label(), n=len(store.traders()),
                 interval=int(effective_interval(len(store.traders()), float(f["interval_s"]))),
                 token=tok, last=last, errors=self.tracker.errors)
        su = links.signup_url()
        return html.escape(text, quote=False) + "\n\n" + t("footer", L, url=a(su, su)), [[(t("back", L), "m:main")]]

    # ---- actions ----------------------------------------------------------------------------
    async def add_handles(self, chat: int, raw: str) -> None:
        L = self.lang()
        ok, bad, dup = [], [], []
        for h in [x.lstrip("@") for x in re.split(r"[\s,;]+", raw) if x.strip("@ ")][:50]:
            try:
                u = await self.api.user_by_handle(h)
            except Unauthorized:
                await self.notify_owner(t("ask_token", L))
                return
            uid = fields.fomo(u, "user.id") if u else None
            if uid is None:
                bad.append(h)
                continue
            handle = fields.fomo(u, "user.handle") or h
            (ok if store.add_trader(str(uid), str(handle), fields.fomo(u, "user.name")) else dup).append(handle)
        await self.say(chat, html.escape(t("added", L, ok=", ".join(ok) or "0", bad=", ".join(bad) or "0",
                                           dup=", ".join(dup) or "0"), quote=False))
        self.tracker.wake.set()

    async def search(self, chat: int, q: str) -> None:
        L = self.lang()
        s = q.strip().lstrip("@").lower()
        rows = [[("@" + x["handle"], f"m:prof:{x['id']}")] for x in store.traders() if s in (x["handle"] or "").lower()]
        try:
            u = await self.api.user_by_handle(s)
            if u and fields.fomo(u, "user.id") is not None and not any(r[0][1].endswith(str(fields.fomo(u, "user.id")))
                                                                        for r in rows):
                h = fields.fomo(u, "user.handle") or s
                rows.append([(f"+ @{h}", f"m:addh:{h}"), (t("profile", L), links.profile_url(str(h)))])
        except Unauthorized:
            pass
        rows.append([(t("back", L), "m:acc:0")])
        await self.show(chat, html.escape(t("search", L) + ": " + q, quote=False) if len(rows) > 1
                        else t("nothing_found", L), rows, msg_id=-1)

    async def import_top(self, chat: int, which: str) -> None:
        L = self.lang()
        periods = ["7d", "30d"] if which == "both" else [which]
        added, skipped = 0, 0
        try:
            for p in periods:
                taken = 0
                for e in await self.api.leaderboard(p):
                    if taken >= 30:
                        break
                    u = fields.fomo(e, "leader.user")
                    trades = fields.num(fields.fomo(e, "leader.trades"))
                    if trades is None and isinstance(u, dict):
                        trades = fields.num(fields.fomo(u, "leader.trades"))
                    uid = fields.fomo(u, "user.id") if isinstance(u, dict) else None
                    if uid is None or trades is None or trades < 20:
                        skipped += 1
                        continue
                    taken += 1
                    if store.add_trader(str(uid), str(fields.fomo(u, "user.handle") or uid), fields.fomo(u, "user.name")):
                        added += 1
        except Unauthorized:
            await self.notify_owner(t("ask_token", L))
            return
        await self.say(chat, html.escape(t("imported", L, ok=added, skip=skipped), quote=False))
        self.tracker.wake.set()

    async def on_callback(self, cq: dict) -> None:
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
        mid = (cq.get("message") or {}).get("message_id")
        await self.tg("answerCallbackQuery", {"callback_query_id": cq["id"]})
        if chat is None or chat != self.owner():
            return
        d = str(cq.get("data") or "")
        parts = d.split(":")
        L = self.lang()
        view: tuple[str, list] | None = None
        if d == "m:main":
            view = self.main_view()
        elif parts[:2] == ["m", "acc"]:
            view = self.accounts_view(int(parts[2]) if len(parts) > 2 else 0)
        elif parts[:2] == ["m", "prof"]:
            view = await self.profile_view(parts[2])
        elif parts[:2] == ["m", "del"]:
            store.delete_trader(parts[2])
            view = self.accounts_view(0)
        elif d == "m:delall":
            view = (html.escape(t("confirm_delete_all", L, n=len(store.traders())), quote=False),
                    [[(t("yes", L), "m:delall_yes"), (t("no", L), "m:acc:0")]])
        elif d == "m:delall_yes":
            store.delete_all_traders()
            view = self.accounts_view(0)
        elif d in ("m:add", "m:search"):
            store.kv_set("await", d[2:])
            view = (html.escape(t("ask_" + d[2:], L), quote=False), [[(t("back", L), "m:acc:0")]])
        elif parts[:2] == ["m", "addh"]:
            await self.add_handles(chat, parts[2])
            view = self.accounts_view(0)
        elif d == "m:imp":
            view = (html.escape(t("import_which", L), quote=False),
                    [[("7d", "m:imp:7d"), ("30d", "m:imp:30d"), (t("both", L), "m:imp:both")],
                     [(t("back", L), "m:acc:0")]])
        elif parts[:2] == ["m", "imp"]:
            await self.import_top(chat, parts[2])
            view = self.accounts_view(0)
        elif d == "m:flt":
            view = self.filters_view()
        elif parts[:2] == ["m", "f"]:
            key = parts[2]
            f = store.filters()
            if key in ("sells", "silent"):
                store.set_filter(key, not f[key])
            elif key in CYCLES:
                c = CYCLES[key]
                i = c.index(f[key]) if f[key] in c else -1
                store.set_filter(key, c[(i + 1) % len(c)])
            view = self.filters_view()
        elif parts[:2] == ["m", "net"]:
            nets = list(store.filters()["networks"])
            nets = [n for n in nets if n != parts[2]] if parts[2] in nets else nets + [parts[2]]
            store.set_filter("networks", nets)
            view = self.filters_view()
        elif parts[:2] == ["m", "rec"]:
            view = self.recent_view(parts[2] if len(parts) > 2 else None)
        elif d == "m:st":
            view = self.status_view()
        elif d == "m:pause":
            store.kv_set("paused", not store.kv_get("paused", False))
            self.tracker.wake.set()
            view = self.main_view()
        elif d == "m:lang":
            store.kv_set("lang", "ru" if L == "en" else "en")
            view = self.main_view()
        if view:
            store.kv_set("menu_msg", mid)
            await self.show(chat, view[0], view[1], msg_id=mid)

    async def on_message(self, msg: dict) -> None:
        chat_obj = msg.get("chat") or {}
        chat = chat_obj.get("id")
        text = (msg.get("text") or "").strip()
        if chat is None or not text:
            return
        owner = self.owner()
        private = chat_obj.get("type") == "private"
        if owner is None:
            if text.split("@")[0] == "/start" and private:
                store.kv_set("owner_chat_id", chat)
                log.info("owner locked")
                v = self.main_view()
                await self.show(chat, v[0], v[1], msg_id=-1)
                if not token():
                    await self.notify_owner(t("ask_token", self.lang()))
            return
        if chat != owner:
            return
        L = self.lang()
        if text.startswith("/token"):
            await self.tg("deleteMessage", {"chat_id": chat, "message_id": msg["message_id"]})
            if not private:
                await self.say(chat, html.escape(t("token_private", L), quote=False))
                return
            value = text[len("/token"):].strip()
            if value:
                set_token(value)
                store.kv_set("token_asked", False)
                self.tracker.wake.set()
                await self.say(chat, html.escape(t("token_saved", L), quote=False))
            return
        if text.split("@")[0] in ("/start", "/menu"):
            v = self.main_view()
            await self.show(chat, v[0], v[1], msg_id=-1)
            return
        pending = store.kv_get("await")
        if pending:
            store.kv_set("await", None)
            if pending == "add":
                await self.add_handles(chat, text)
                v = self.accounts_view(0)
                await self.show(chat, v[0], v[1], msg_id=-1)
            elif pending == "search":
                await self.search(chat, text)
            return
        v = self.main_view()
        await self.show(chat, v[0], v[1], msg_id=-1)

    async def poll(self) -> None:
        offset = int(store.kv_get("tg_offset", 0) or 0)
        while True:
            res = await self.tg("getUpdates", {"offset": offset, "timeout": 50,
                                               "allowed_updates": ["message", "callback_query"]},
                                timeout=65, retries=1)
            if res is None:
                await asyncio.sleep(5)
                continue
            for u in res:
                offset = max(offset, int(u["update_id"]) + 1)
                store.kv_set("tg_offset", offset)
                try:
                    if "callback_query" in u:
                        await self.on_callback(u["callback_query"])
                    elif "message" in u:
                        await self.on_message(u["message"])
                except Exception:
                    log.exception("update failed")

    async def run(self) -> None:
        await asyncio.gather(self.poll(), self.tracker.run())


def setup_logging() -> None:
    from desk.main import LOGS, Redact
    LOGS.mkdir(parents=True, exist_ok=True)
    h = TimedRotatingFileHandler(LOGS / "copybot.log", when="midnight", backupCount=14, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    h.addFilter(Redact())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    from desk.main import load_env
    load_env()
    setup_logging()
    if not os.environ.get("COPYBOT_TG_TOKEN"):
        print("COPYBOT_TG_TOKEN is not set (see .env.example)", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(Bot().run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
