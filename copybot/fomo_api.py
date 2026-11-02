"""FOMO's own (unofficial) app API, read-only. Never places orders.

Auth: `Authorization: Bearer <FOMO_TOKEN>`, a Privy token of a SEPARATE EMPTY FOMO account
(lives about 60 min). Without it every call is 401 {"error":"unauthorized"} (verified).
The first authenticated response of each endpoint is saved to logs/raw/fomo_<endpoint>.json so
copybot/fields.py can be verified. Keys are read only through copybot/fields.py.
Global rate limit: 2 requests per second.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from . import fields, store
from .links import FOMO_API

log = logging.getLogger("copybot.fomo")

BASE = FOMO_API
GROKHOOD = "https://api.grokhood.tech/v1"
RAW = Path(__file__).resolve().parent.parent / "logs" / "raw"


class Unauthorized(Exception):
    pass


class RateLimiter:
    def __init__(self, per_s: float) -> None:
        self.gap = 1.0 / per_s
        self.next = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            if self.next > now:
                await asyncio.sleep(self.next - now)
            self.next = max(now, self.next) + self.gap


def token() -> str:
    return (store.kv_get("fomo_token") or os.environ.get("FOMO_TOKEN") or "").strip()


def token_age_s() -> float | None:
    t = store.kv_get("fomo_token_set_at")
    if t is None:
        return None
    return time.time() - float(t)


def set_token(value: str) -> None:
    store.kv_set("fomo_token", value.strip())
    store.kv_set("fomo_token_set_at", time.time())
    store.kv_set("token_warned", False)
    store.kv_set("need_token", False)


class FomoAPI:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10))
        self.limiter = RateLimiter(2.0)
        self._tok_cache: dict[str, tuple[float, dict]] = {}
        self._gh_cache: dict[str, tuple[float, dict | None]] = {}
        self.requests = 0

    def _save_raw(self, name: str, data: Any) -> None:
        p = RAW / f"fomo_{name}.json"
        if p.exists():
            return
        try:
            RAW.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=1)[:2_000_000], encoding="utf-8")
            log.info("saved raw sample %s", p.name)
            self._write_fields_report()
        except OSError:
            pass

    def _write_fields_report(self) -> None:
        samples = {}
        for n in ("user", "swaps", "balances", "leaderboard", "filterTokens"):
            p = RAW / f"fomo_{n}.json"
            if p.exists():
                try:
                    samples[n] = json.loads(p.read_text())
                except ValueError:
                    pass
        (RAW / "fomo_fields_report.json").write_text(json.dumps(fields.verify(samples), indent=1))

    async def _req(self, method: str, path: str, name: str, body: Any = None) -> Any:
        tok = token()
        if not tok:
            raise Unauthorized("no token")
        await self.limiter.wait()
        self.requests += 1
        r = await self.client.request(method, BASE + path, json=body,
                                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
        if r.status_code == 401:
            raise Unauthorized("401")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        self._save_raw(name, data)
        return data

    async def user_by_handle(self, handle: str) -> dict | None:
        h = handle.strip().lstrip("@")
        data = await self._req("GET", f"/v2/users/userHandle/{quote(h, safe='')}", "user")
        if data is None:
            return None
        u = fields.fomo(data, "user.root")
        return u if isinstance(u, dict) else None

    async def swaps(self, user_id: str) -> list[dict]:
        data = await self._req("GET", f"/v2/users/{quote(str(user_id), safe='')}/swaps", "swaps")
        return [s for s in fields.fomo_list(data, "swaps.list") if isinstance(s, dict)]

    async def balances(self, user_id: str) -> list[dict]:
        data = await self._req("GET", f"/v2/users/{quote(str(user_id), safe='')}/balances", "balances")
        return [b for b in fields.fomo_list(data, "balances.list") if isinstance(b, dict)]

    async def leaderboard(self, period: str) -> list[dict]:
        assert period in ("24h", "7d", "30d")
        data = await self._req("GET", f"/v2/leaderboard/{period}", "leaderboard")
        return [e for e in fields.fomo_list(data, "leaders.list") if isinstance(e, dict)]

    async def filter_tokens(self, keys: list[str]) -> dict[str, dict]:
        """keys: "<address>:<chainId>". Batched by 20, cached 60s. Returns key -> token dict."""
        now = time.time()
        out: dict[str, dict] = {}
        todo = []
        for k in dict.fromkeys(keys):
            hit = self._tok_cache.get(k)
            if hit and now - hit[0] < 60:
                out[k] = hit[1]
            else:
                todo.append(k)
        for i in range(0, len(todo), 20):
            batch = todo[i:i + 20]
            data = await self._req("POST", "/proxy/filterTokens", "filterTokens", batch)
            for tok in fields.fomo_list(data, "tokens.list"):
                if not isinstance(tok, dict):
                    continue
                addr = fields.fomo(tok, "token.address")
                cid = fields.fomo(tok, "token.chain_id")
                if addr is None:
                    continue
                match = next((k for k in batch if k.split(":", 1)[0].lower() == str(addr).lower()
                              and (cid is None or k.split(":", 1)[1] == str(cid))), None)
                if match:
                    out[match] = tok
                    self._tok_cache[match] = (now, tok)
        return out

    async def grokhood_token(self, address: str) -> dict | None:
        """GROKHOOD token card (Robinhood Chain). Public, no auth. Cached 5 min."""
        now = time.time()
        hit = self._gh_cache.get(address.lower())
        if hit and now - hit[0] < 300:
            return hit[1]
        try:
            r = await self.client.get(f"{GROKHOOD}/tokens/{address}", timeout=10)
            data = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            data = None
        self._gh_cache[address.lower()] = (now, data)
        return data
