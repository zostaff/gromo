"""Notifier: one push fans out to Telegram (owner chat) and Web Push (db.push_subs).

`await push(text, buttons, mint=None, kind="info")`
buttons: list of (label, target). target is an https URL (url button) or "cb:..." (callback button,
handled by desk/telegram.py -> desk/actions.py). Channels without buttons get each URL as a line.
PAPER pushes start with "PAPER · ". Secrets come from env only and are never logged.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from typing import Any

import httpx

from . import config, db

log = logging.getLogger("notify")

TG_API = "https://api.telegram.org"
APP_TITLE = "GROMO"
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10))
    return _client


def owner_chat_id() -> int | None:
    v = db.kv_get("owner_chat_id")
    return int(v) if v is not None else None


# ---- Telegram -----------------------------------------------------------------
async def tg_call(method: str, payload: dict | None = None, files: dict | None = None,
                  timeout: float = 20, retries: int = 3) -> dict | None:
    """Call the Telegram Bot API. Returns `result` or None. Never logs the token."""
    token = os.environ.get("TG_TOKEN", "").strip()
    if not token:
        return None
    url = f"{TG_API}/bot{token}/{method}"
    last = ""
    for attempt in range(retries):
        try:
            if files:
                r = await client().post(url, data=payload or {}, files=files, timeout=timeout)
            else:
                r = await client().post(url, json=payload or {}, timeout=timeout)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and body.get("ok"):
                return body.get("result")
            last = f"{r.status_code} {body.get('description', '')}"
            if r.status_code == 429:
                wait = float((body.get("parameters") or {}).get("retry_after", 2))
                await asyncio.sleep(min(wait, 30))
                continue
            if 400 <= r.status_code < 500:
                break  # bad request: retrying will not help
        except (httpx.HTTPError, ValueError) as e:
            last = type(e).__name__
        await asyncio.sleep(1.5 * (attempt + 1))
    log.warning("telegram %s failed: %s", method, last)
    return None


def keyboard(buttons: list[tuple[str, str]]) -> dict | None:
    """Inline keyboard: URL buttons on the first row(s), callback buttons on their own row."""
    urls = [{"text": l, "url": t} for l, t in buttons if not str(t).startswith("cb:")]
    cbs = [{"text": l, "callback_data": t[:64]} for l, t in buttons if str(t).startswith("cb:")]
    rows = [urls[i:i + 2] for i in range(0, len(urls), 2)]
    if cbs:
        rows.append(cbs)
    return {"inline_keyboard": rows} if rows else None


def tg_html(text: str) -> str:
    """Escape plain text for Telegram HTML parse mode."""
    return html.escape(text, quote=False)


async def tg_send(chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None,
                  image: str | None = None, is_html: bool = False) -> dict | None:
    body = text if is_html else tg_html(text)
    markup = keyboard(buttons or [])
    base: dict[str, Any] = {"chat_id": chat_id, "parse_mode": "HTML"}
    if markup:
        base["reply_markup"] = markup
    if image and len(body) <= 1024:
        res = await tg_call("sendPhoto", {**base, "photo": image, "caption": body})
        if res is not None:
            return res
    return await tg_call("sendMessage", {**base, "text": body[:4096], "disable_web_page_preview": True})


# ---- Web Push -------------------------------------------------------------------
def vapid_ready() -> bool:
    return bool(os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_PUBLIC_KEY"))


def _webpush_one(sub: dict, payload: str) -> int:
    """Blocking (run in a thread). Returns HTTP status (0 = network error)."""
    from pywebpush import WebPushException, webpush
    info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
    try:
        r = webpush(info, payload, vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": os.environ.get("VAPID_SUB", "mailto:desk@grokhood.tech")},
                    ttl=3600, timeout=15)
        return getattr(r, "status_code", 201)
    except WebPushException as e:
        return getattr(e.response, "status_code", 0) if e.response is not None else 0
    except Exception:  # noqa: BLE001 - network/crypto errors must not kill the notifier
        return 0


async def webpush_all(body: str, url: str | None) -> int:
    if not vapid_ready():
        return 0
    payload = json.dumps({"title": APP_TITLE, "body": body, "url": url or "/"})
    sent = 0
    for sub in db.q("SELECT endpoint, p256dh, auth FROM push_subs"):
        status = 0
        for attempt in range(3):
            status = await asyncio.to_thread(_webpush_one, sub, payload)
            if status in (404, 410) or 200 <= status < 300 or status in (400, 401, 403, 413):
                break
            await asyncio.sleep(1.5 * (attempt + 1))
        if status in (404, 410):
            db.x("DELETE FROM push_subs WHERE endpoint=?", (sub["endpoint"],))
            log.info("web push subscription gone, removed")
        elif 200 <= status < 300:
            sent += 1
        else:
            log.warning("web push failed: %s", status)
    return sent


# ---- fan-out --------------------------------------------------------------------
def prefixed(text: str) -> str:
    if config.get("desk.mode") == "PAPER" and not text.startswith("PAPER · "):
        return "PAPER · " + text
    return text


def with_url_lines(text: str, buttons: list[tuple[str, str]]) -> str:
    lines = [f"{l}: {t}" for l, t in buttons if not str(t).startswith("cb:")]
    return text + ("\n" + "\n".join(lines) if lines else "")


async def push(text: str, buttons: list[tuple[str, str]] | None = None, mint: str | None = None,
               kind: str = "info") -> None:
    buttons = list(buttons or [])
    text = prefixed(text)
    image = None
    if mint:
        c = db.coin(mint)
        image = (c or {}).get("image") or None
        db.kv_set(f"push:{mint}", {"text": text, "buttons": buttons, "kind": kind, "ts": db.now()})
    first_url = next((t for _, t in buttons if not str(t).startswith("cb:")), None)

    chat = owner_chat_id()
    tasks = []
    if chat is not None and os.environ.get("TG_TOKEN"):
        tasks.append(tg_send(chat, text, buttons, image=image))
    tasks.append(webpush_all(with_url_lines(text, buttons), first_url))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.warning("push channel error: %s", type(r).__name__)
    db.kv_set("unread", int(db.kv_get("unread", 0) or 0) + 1)
    log.info("push kind=%s mint=%s", kind, mint)
