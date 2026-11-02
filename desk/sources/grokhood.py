"""GROKHOOD API client (Robinhood Chain Pons launches). Base from config desk.grokhood_api.

REST: /v1/launches, /v1/tokens/:a, /holders, /map, /alerts, /chart, /v1/scoring.
SSE: /v1/stream events launch, score, dump (also insiders, prelaunch, operator, x_activity; ignored
by the caller unless wanted). Reconnects with backoff 1,2,4..60 s, and when silent for 60 s
(the server pings every 15 s).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from desk import config, db
from desk.sources import UA, request_json

log = logging.getLogger("grokhood")

_client: httpx.AsyncClient | None = None
SILENT_S = 60.0


def base() -> str:
    return str(config.get("desk.grokhood_api")).rstrip("/")


def _c() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30, headers=UA)
    return _client


async def _get(path: str, **params: Any) -> Any:
    return await request_json(_c(), "GET", f"{base()}{path}", params=params or None, name="grokhood")


async def launches(limit: int = 100, cursor: str | None = None) -> dict | None:
    p: dict[str, Any] = {"limit": limit}
    if cursor:
        p["cursor"] = cursor
    return await _get("/v1/launches", **p)


async def token(addr: str) -> dict | None:
    return await _get(f"/v1/tokens/{addr}")


async def holders(addr: str, limit: int = 50) -> dict | None:
    return await _get(f"/v1/tokens/{addr}/holders", limit=limit)


async def token_map(addr: str) -> dict | None:
    return await _get(f"/v1/tokens/{addr}/map")


async def alerts(addr: str) -> dict | None:
    return await _get(f"/v1/tokens/{addr}/alerts")


async def chart(addr: str) -> dict | None:
    return await _get(f"/v1/tokens/{addr}/chart")


async def scoring() -> dict | None:
    return await _get("/v1/scoring")


def _note(name: str) -> None:
    try:
        from desk import report
        fn = getattr(report, "note_reconnect", None)
        if callable(fn):
            fn(name)
    except Exception:  # noqa: BLE001 - optional hook
        pass


async def stream(events: set[str] | None = None) -> AsyncIterator[tuple[str, dict]]:
    """Yields (event, data) forever. Reconnect count in db.kv("grokhood_reconnects")."""
    backoff = 1.0
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=SILENT_S), headers=UA) as c:
                async with c.stream("GET", f"{base()}/v1/stream",
                                    headers={"accept": "text/event-stream"}) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"stream HTTP {r.status_code}")
                    log.info("grokhood stream connected")
                    backoff = 1.0
                    ev, data = "message", []
                    async for line in r.aiter_lines():
                        if line == "":
                            if data:
                                raw = "\n".join(data)
                                if events is None or ev in events:
                                    try:
                                        d = json.loads(raw)
                                    except ValueError:
                                        d = None
                                    if isinstance(d, dict):
                                        yield ev, d
                            ev, data = "message", []
                        elif line.startswith(":"):
                            continue
                        elif line.startswith("event:"):
                            ev = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip())
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - any failure = reconnect
            log.warning("grokhood stream dropped: %s", type(e).__name__)
        db.kv_set("grokhood_reconnects", int(db.kv_get("grokhood_reconnects", 0) or 0) + 1)
        _note("grokhood")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)
