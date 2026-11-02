"""External data sources. Shared HTTP helper: retries, 429 backoff 30 s."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("sources")

BACKOFF_429_S = 30.0
UA = {"user-agent": "grokhood-copyfomo-desk/1.0", "accept": "application/json"}


class SourceError(RuntimeError):
    """A source did not answer with usable data after retries."""


async def request_json(client: httpx.AsyncClient, method: str, url: str, *, tries: int = 3,
                       name: str = "http", sleep=asyncio.sleep, **kw: Any) -> Any:
    """JSON answer or SourceError. 429 waits 30 s (Retry-After if larger), 5xx/network wait 1,2,4 s.
    404 returns None (the source does not know this address)."""
    last = ""
    for attempt in range(tries):
        try:
            r = await client.request(method, url, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = f"{type(e).__name__}"
            await sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            wait = BACKOFF_429_S
            try:
                wait = max(wait, float(ra)) if ra else wait
            except ValueError:
                pass
            last = "429"
            log.warning("%s 429, backing off %.0fs", name, wait)
            await sleep(wait)
            continue
        if r.status_code == 404:
            return None
        if r.status_code >= 500:
            last = str(r.status_code)
            await sleep(2 ** attempt)
            continue
        if r.status_code >= 400:
            raise SourceError(f"{name} {r.status_code}")
        try:
            return r.json()
        except ValueError:
            last = "bad json"
            await sleep(2 ** attempt)
    raise SourceError(f"{name} failed: {last}")
