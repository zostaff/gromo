"""Scanner: kill order, copycat normalization, waiting room. No network."""
from __future__ import annotations

import asyncio
import functools

import pytest

from desk import bus, config, db
from desk.agents import scanner


def sync(fn):
    """Run an async test with asyncio.run (pytest-asyncio is not installed)."""
    @functools.wraps(fn)
    def w(*a, **kw):
        return asyncio.run(fn(*a, **kw))
    return w


def setv(k, v):
    config._cache[k] = config.coerce(k, v)


@pytest.fixture(autouse=True)
def clean():
    scanner.room.clear()
    scanner._top10_cache.clear()
    yield
    scanner.room.clear()


def base(**kw):
    c = {"liq": 50000.0, "top10": 12.0, "h1": 20.0, "age_min": 15.0, "waited_min": 15.0, "copycat": None}
    c.update(kw)
    return c


def test_pass():
    assert scanner.evaluate(base()) == ("pass", None)


def test_kill_order():
    cc = {"name": "PEPE", "h24": 900.0}
    # every check failing: liquidity comes first (wait), then top 10, 1h change, copycat
    assert scanner.evaluate(base(liq=1000.0, top10=60.0, h1=500.0, copycat=cc))[0] == "wait"
    assert scanner.evaluate(base(top10=60.0, h1=500.0, copycat=cc)) == ("kill", "top 10 hold 60.0% > 25%")
    assert scanner.evaluate(base(h1=500.0, copycat=cc)) == ("kill", "1h change +500% > 80%")
    assert scanner.evaluate(base(copycat=cc)) == ("kill", "copycat of PEPE (+900% 24h)")


def test_wait_then_never_qualified():
    v, r = scanner.evaluate(base(liq=10000.0, waited_min=5))
    assert v == "wait" and r == "liquidity $10,000 < $15,000"
    assert scanner.evaluate(base(liq=10000.0, waited_min=60)) == ("kill", "never qualified: liquidity $10,000 < $15,000")
    assert scanner.evaluate(base(age_min=3.0, waited_min=3)) == ("wait", "pool 3 min < 10 min")
    assert scanner.evaluate(base(top10=None))[1] == "top 10 n/a"


def test_robinhood_gate_skips_top10():
    setv("scanner.rh_min_liquidity_usd", 5000)
    assert scanner.evaluate(base(liq=6000.0, top10=None), "robinhood") == ("pass", None)
    assert scanner.evaluate(base(liq=4000.0, top10=None), "robinhood")[0] == "wait"


def test_normalize_name():
    n = scanner.normalize_name
    assert n("The Pepe Coin!") == n("PEPE") == n("pépé") == n("$pepe") == "pepe"
    assert n("Dog Wif Hat") == n("dogwifhat")
    assert n("PEPE token") == "pepe"
    assert n("Solana") == "solana"
    assert n(None) == ""


def test_copycat():
    runners = {"RunMint": {"name": "Pepe Coin", "symbol": "PEPE", "h24": 900.0}}
    assert scanner.copycat_of("New1", "the pepe", "PEPE2", runners)["mint"] == "RunMint"
    assert scanner.copycat_of("New2", "Frog", "pépé", runners)["mint"] == "RunMint"
    assert scanner.copycat_of("New3", "Doge", "DOGE", runners) is None
    assert scanner.copycat_of("RunMint", "Pepe Coin", "PEPE", runners) is None  # itself


def test_runners_today_threshold():
    scanner.note_runner("A", "Alpha", "ALP", 450.0)
    scanner.note_runner("B", "Beta", "BET", 120.0)
    assert set(scanner.runners_today()) == {"A"}


@sync
async def test_poll_pass_and_kill(monkeypatch):
    now = db.now()
    pairs = {
        "Good": [{"pairAddress": "PoolG", "dexId": "pumpswap", "baseToken": {"address": "Good", "name": "Good", "symbol": "GD"},
                  "priceUsd": "0.001", "liquidity": {"usd": 60000}, "priceChange": {"h1": 10, "h24": 10},
                  "volume": {"m5": 100}, "pairCreatedAt": (now - 20 * 60) * 1000}],
        "Whale": [{"pairAddress": "PoolW", "dexId": "pumpswap", "baseToken": {"address": "Whale", "name": "Whale", "symbol": "WH"},
                   "priceUsd": "0.002", "liquidity": {"usd": 60000}, "priceChange": {"h1": 10, "h24": 10},
                   "pairCreatedAt": (now - 20 * 60) * 1000}],
    }

    async def dex_pairs(chain, mints):
        return {m: pairs.get(m, []) for m in mints}

    async def top10_of(mint, pool):
        return 40.0 if mint == "Whale" else 15.0

    monkeypatch.setattr(scanner, "dex_pairs", dex_pairs)
    monkeypatch.setattr(scanner, "top10_of", top10_of)
    scanner.enter("Good", "solana", now - 20 * 60)
    scanner.enter("Whale", "solana", now - 20 * 60)
    await scanner.poll_chain("solana")
    msg = bus.auditor.get_nowait()
    assert msg["mint"] == "Good" and msg["verdict"] == "pass" and msg["pool"] == "PoolG"
    assert msg["chain"] == "solana" and msg["top10Pct"] == 15.0 and msg["ageMin"] == 20
    assert bus.auditor.empty()
    rows = {r["mint"]: r for r in db.q("SELECT * FROM scans")}
    assert rows["Whale"]["verdict"] == "kill" and rows["Whale"]["reason"] == "top 10 hold 40.0% > 25%"
    assert db.q1("SELECT COUNT(*) AS c FROM prices")["c"] == 2
    feed = db.kv_get("scanner_feed")
    assert {f["mint"] for f in feed} == {"Good", "Whale"}
    assert db.kv_get("scanner_card")["pools"] == 2
    # one pass per mint per night
    assert scanner.enter("Good", "solana", now) is False
