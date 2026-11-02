"""Copy bot storage: its own SQLite file data/copybot.db (separate from the desk db)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("COPYBOT_DB", ROOT / "data" / "copybot.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS traders (id TEXT PRIMARY KEY, handle TEXT, name TEXT, added_at REAL,
  initialized INTEGER DEFAULT 0, last_poll REAL);
CREATE TABLE IF NOT EXISTS seen (swap_id TEXT PRIMARY KEY, trader_id TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS trades (swap_id TEXT PRIMARY KEY, trader_id TEXT, handle TEXT, ts REAL,
  side TEXT, address TEXT, chain TEXT, symbol TEXT, name TEXT, usd REAL, mcap REAL, liq REAL, sent INTEGER);
CREATE INDEX IF NOT EXISTS trades_ts ON trades(ts);
CREATE TABLE IF NOT EXISTS breaking (coin TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_FILTERS = {"min_trade_usd": 50, "min_mcap_usd": 50_000, "min_liq_usd": 20_000,
                   "networks": ["solana", "robinhood", "base", "bnb"], "sells": True, "silent": False,
                   "breaking_n": 3, "breaking_window_min": 30, "interval_s": 30}

_conn: sqlite3.Connection | None = None


def use(path: str | Path) -> None:
    global _conn, DB_PATH
    if _conn is not None:
        _conn.close()
    DB_PATH = Path(path)
    _conn = None
    conn()


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        if str(DB_PATH) != ":memory:":
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), isolation_level=None, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        if str(DB_PATH) != ":memory:":
            _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def q(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, tuple(args)).fetchall()]


def q1(sql: str, args: Iterable[Any] = ()) -> dict | None:
    r = conn().execute(sql, tuple(args)).fetchone()
    return dict(r) if r else None


def x(sql: str, args: Iterable[Any] = ()) -> int:
    return conn().execute(sql, tuple(args)).lastrowid


def kv_get(key: str, default: Any = None) -> Any:
    r = q1("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value: Any) -> None:
    x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))


def filters() -> dict:
    f = dict(DEFAULT_FILTERS)
    f.update(kv_get("filters", {}) or {})
    return f


def set_filter(key: str, value: Any) -> None:
    f = kv_get("filters", {}) or {}
    f[key] = value
    kv_set("filters", f)


# ---- traders ------------------------------------------------------------------------
def traders() -> list[dict]:
    return q("SELECT * FROM traders ORDER BY added_at, handle")


def add_trader(id_: str, handle: str, name: str | None = None) -> bool:
    if q1("SELECT 1 FROM traders WHERE id=?", (id_,)):
        return False
    x("INSERT INTO traders(id,handle,name,added_at) VALUES(?,?,?,?)", (id_, handle, name, time.time()))
    return True


def delete_trader(id_: str) -> None:
    x("DELETE FROM traders WHERE id=?", (id_,))


def delete_all_traders() -> None:
    x("DELETE FROM traders")


def mark_seen(swap_ids: list[str], trader_id: str) -> None:
    t = time.time()
    conn().executemany("INSERT OR IGNORE INTO seen(swap_id,trader_id,ts) VALUES(?,?,?)",
                       [(s, trader_id, t) for s in swap_ids])


def is_seen(swap_id: str) -> bool:
    return q1("SELECT 1 FROM seen WHERE swap_id=?", (swap_id,)) is not None


def save_trade(t: dict, sent: bool) -> None:
    x("INSERT OR IGNORE INTO trades(swap_id,trader_id,handle,ts,side,address,chain,symbol,name,usd,mcap,liq,sent) "
      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (t["swap_id"], t["trader_id"], t["handle"], t["ts"], t["side"], t["address"], t["chain"], t["symbol"],
       t["name"], t["usd"], t["mcap"], t["liq"], int(sent)))


def recent_trades(since: float, limit: int = 20) -> list[dict]:
    return q("SELECT * FROM trades WHERE ts>=? ORDER BY ts DESC LIMIT ?", (since, limit))
