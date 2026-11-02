"""PumpPortal migrations: ONE websocket to wss://pumpportal.fun/api/data, {"method":"subscribeMigration"}.

Reconnect backoff 1,2,4..60 s; reconnect if silent 120 s. Reconnects counted in db.kv("pumpportal_reconnects").
Real message (logs/raw/pumpportal_migration.json): {"signature","mint","txType":"migrate","pool":"pump-amm"}.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import websockets

from desk import db
from desk import fields as F

log = logging.getLogger("pumpportal")

URL = "wss://pumpportal.fun/api/data"
SILENT_S = 120.0


def _bump() -> int:
    n = int(db.kv_get("pumpportal_reconnects", 0) or 0) + 1
    db.kv_set("pumpportal_reconnects", n)
    return n


def _note(name: str) -> None:
    try:
        from desk import report
        fn = getattr(report, "note_reconnect", None)
        if callable(fn):
            fn(name)
    except Exception:  # noqa: BLE001 - optional hook
        pass


async def migrations() -> AsyncIterator[dict]:
    """Yields migration messages forever."""
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(URL, ping_interval=30, ping_timeout=30, open_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                log.info("pumpportal connected, subscribed to migrations")
                backoff = 1.0
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=SILENT_S)
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and F.pp_is_migration(msg):
                        yield msg
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log.warning("pumpportal silent %.0fs, reconnecting", SILENT_S)
        except Exception as e:  # noqa: BLE001
            log.warning("pumpportal dropped: %s", type(e).__name__)
        n = _bump()
        _note("pumpportal")
        log.info("pumpportal reconnect #%d in %.0fs", n, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)
