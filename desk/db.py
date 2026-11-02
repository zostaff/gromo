"""SQLite (WAL) schema + helpers. One connection, used from the single asyncio loop.

Every coin row carries `chain` ("solana" | "robinhood"). Money in USD floats, times in unix seconds.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DESK_DB", ROOT / "data" / "desk.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS coins (mint TEXT PRIMARY KEY, chain TEXT NOT NULL DEFAULT 'solana',
  name TEXT, symbol TEXT, pool TEXT, migrated_at REAL, creator TEXT,
  creator_balance_at_migration REAL, holders INTEGER, image TEXT, first_seen REAL,
  liq_at_scan REAL, extra TEXT);
CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, mint TEXT, ts REAL, liquidity_usd REAL,
  top10_pct REAL, h1_change_pct REAL, age_min REAL, verdict TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS audits (id INTEGER PRIMARY KEY, mint TEXT, ts REAL, layer INTEGER,
  name TEXT, value_text TEXT, pass INTEGER, source TEXT, ms INTEGER);
CREATE TABLE IF NOT EXISTS verdicts (id INTEGER PRIMARY KEY, mint TEXT, ts REAL, alive_6h REAL,
  organic REAL, flush REAL, chase REAL, score INTEGER, pass INTEGER, reason TEXT,
  input_tokens INTEGER, model TEXT, cost_usd REAL);
CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY, mint TEXT, ts REAL, price REAL,
  size_usd REAL, stack_pct REAL, link TEXT, mode TEXT, status TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY, mint TEXT, opened_at REAL,
  entry_price REAL, size_usd REAL, peak_price REAL, stop_pct REAL, stop_price REAL,
  trail_active INTEGER DEFAULT 0, closed_at REAL, exit_price REAL, exit_reason TEXT,
  pnl_usd REAL, fees_usd REAL, mode TEXT, status TEXT DEFAULT 'open', last_price REAL,
  no_price_polls INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, mint TEXT,
  agent TEXT, title TEXT, subtitle TEXT, value TEXT, value2 TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS prices (mint TEXT, ts REAL, price REAL, volume_m5 REAL);
CREATE INDEX IF NOT EXISTS prices_mint_ts ON prices(mint, ts);
CREATE TABLE IF NOT EXISTS balance (ts REAL, value REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS change_log (ts REAL, key TEXT, old TEXT, new TEXT, who TEXT);
CREATE TABLE IF NOT EXISTS skips (mint TEXT PRIMARY KEY, until REAL);
CREATE TABLE IF NOT EXISTS heartbeats (agent TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS paused (agent TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, p256dh TEXT, auth TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS nights (id INTEGER PRIMARY KEY, started_at REAL, start_balance REAL,
  mode TEXT, ended_at REAL, report_path TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

_conn: sqlite3.Connection | None = None


def _open(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    if path != ":memory:":
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(SCHEMA)
    return c


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = _open(str(DB_PATH))
    return _conn


def use(path: str | Path) -> None:
    """Point at another db file (tests use ':memory:')."""
    global _conn, DB_PATH
    if _conn is not None:
        _conn.close()
    DB_PATH = Path(path)
    _conn = _open(str(path))


def now() -> float:
    return time.time()


def q(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, tuple(args)).fetchall()]


def q1(sql: str, args: Iterable[Any] = ()) -> dict | None:
    r = conn().execute(sql, tuple(args)).fetchone()
    return dict(r) if r else None


def x(sql: str, args: Iterable[Any] = ()) -> int:
    return conn().execute(sql, tuple(args)).lastrowid


def insert(table: str, row: dict) -> int:
    cols = ",".join(row)
    return x(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(row))})", row.values())


def update(table: str, id_: int, row: dict) -> None:
    sets = ",".join(f"{k}=?" for k in row)
    x(f"UPDATE {table} SET {sets} WHERE id=?", [*row.values(), id_])


def upsert_coin(row: dict) -> None:
    cols = ",".join(row)
    upd = ",".join(f"{k}=COALESCE(excluded.{k},{k})" for k in row if k != "mint")
    x(f"INSERT INTO coins ({cols}) VALUES ({','.join('?' * len(row))}) "
      f"ON CONFLICT(mint) DO UPDATE SET {upd}", row.values())


def coin(mint: str) -> dict | None:
    return q1("SELECT * FROM coins WHERE mint=?", (mint,))


# ---- settings -------------------------------------------------------------
def settings_rows() -> dict[str, str]:
    return {r["key"]: r["value"] for r in q("SELECT key, value FROM settings")}


def save_setting(key: str, old: Any, new: Any, who: str) -> None:
    t = now()
    x("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
      "value=excluded.value, updated_at=excluded.updated_at", (key, json.dumps(new), t))
    insert("change_log", {"ts": t, "key": key, "old": json.dumps(old), "new": json.dumps(new), "who": who})


# ---- heartbeats / pause ---------------------------------------------------
def beat(agent: str) -> None:
    x("INSERT INTO heartbeats(agent,ts) VALUES(?,?) ON CONFLICT(agent) DO UPDATE SET ts=excluded.ts",
      (agent, now()))


def is_paused(agent: str) -> bool:
    return q1("SELECT 1 AS p FROM paused WHERE agent=?", (agent,)) is not None


def set_paused(agent: str, paused: bool) -> None:
    if paused:
        x("INSERT OR IGNORE INTO paused(agent) VALUES(?)", (agent,))
    else:
        x("DELETE FROM paused WHERE agent=?", (agent,))


# ---- activity / prices / balance -------------------------------------------
def add_activity(kind: str, mint: str, agent: str, title: str, subtitle: str,
                 value: str = "", value2: str = "", mode: str | None = None) -> dict:
    """kind: bought | sold | stopped | rejected | skipped | exit."""
    row = {"ts": now(), "kind": kind, "mint": mint, "agent": agent, "title": title,
           "subtitle": subtitle, "value": value, "value2": value2, "mode": mode}
    row["id"] = insert("activity", row)
    from . import bus
    bus.emit("activity", row)
    return row


def add_price(mint: str, price: float | None, volume_m5: float | None, ts: float | None = None) -> None:
    if price is None:
        return
    insert("prices", {"mint": mint, "ts": ts or now(), "price": float(price),
                      "volume_m5": float(volume_m5) if volume_m5 is not None else None})


def open_positions() -> list[dict]:
    return q("SELECT * FROM positions WHERE status IN ('open','exiting') ORDER BY opened_at")


def realized_pnl(mode: str = "PAPER") -> float:
    r = q1("SELECT COALESCE(SUM(pnl_usd),0) AS s FROM positions WHERE status='closed' AND mode=?", (mode,))
    return float(r["s"]) if r else 0.0


def paper_balance() -> float:
    """PAPER balance = start stack + realized PnL (net of fees) + unrealized on open positions."""
    from . import config
    bal = float(config.get("desk.paper_start_stack")) + realized_pnl("PAPER")
    for p in open_positions():
        if p["mode"] == "PAPER":
            last = p.get("last_price") or p["entry_price"]
            bal += p["size_usd"] * (last / p["entry_price"] - 1)
    return round(bal, 2)


def kv_get(key: str, default: Any = None) -> Any:
    r = q1("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value: Any) -> None:
    x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))


def current_night() -> dict | None:
    return q1("SELECT * FROM nights WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")


def start_night(balance: float, mode: str) -> dict:
    nid = insert("nights", {"started_at": now(), "start_balance": balance, "mode": mode})
    return q1("SELECT * FROM nights WHERE id=?", (nid,))
