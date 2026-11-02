"""Auditor: layer order, stop at first red, bundling components, delegation grouping. No network."""
from __future__ import annotations

import json
from pathlib import Path

import asyncio
import functools

import pytest

from desk import bus, config, db
from desk.agents import auditor

RAW = Path(__file__).resolve().parents[2] / "logs" / "raw"
MINT = "Mint1111111111111111111111111111111111pump"
POOL = "Pool111111111111111111111111111111111111111"


def sync(fn):
    """Run an async test with asyncio.run (pytest-asyncio is not installed)."""
    @functools.wraps(fn)
    def w(*a, **kw):
        return asyncio.run(fn(*a, **kw))
    return w


def setv(k, v):
    config._cache[k] = config.coerce(k, v)


def scan(**kw):
    m = {"agent": "scanner", "chain": "solana", "mint": MINT, "name": "Test", "symbol": "TST", "pool": POOL,
         "liquidityUsd": 50000.0, "top10Pct": 12.0, "h1ChangePct": 10.0, "ageMin": 15, "verdict": "pass",
         "reason": None, "ts": "2026-11-02T00:00:00+00:00", "image": None}
    m.update(kw)
    return m


@pytest.fixture
def fakes(monkeypatch):
    st = {"mint": {"mintAuthority": None, "freezeAuthority": None, "supply": "1000000000000000", "decimals": 6},
          "bal": 0, "top10": {"pct": 12.0}, "graph": [],
          "report": {"creator": "Dev1", "markets": [{"pubkey": POOL, "lp": {"lpLockedPct": 100}}],
                     "totalHolders": 500},
          "pair": {"pairAddress": POOL, "liquidity": {"usd": 48000.0}, "pairCreatedAt": (db.now() - 1800) * 1000},
          "accounts": [], "calls": []}

    async def mint_info(m):
        st["calls"].append("mint")
        return st["mint"]

    async def bal(o, m):
        return st["bal"]

    async def top10(m, p):
        return st["top10"]

    async def report(m):
        return st["report"]

    async def graph(m):
        return st["graph"]

    async def pair(c, m, p):
        return st["pair"]

    async def accounts(m):
        st["calls"].append("gpa")
        if isinstance(st["accounts"], Exception):
            raise st["accounts"]
        return st["accounts"]

    async def nosleep(s):
        return None

    for n, f in (("src_mint_info", mint_info), ("src_balance", bal), ("src_top10", top10), ("src_report", report),
                 ("src_graph", graph), ("src_pair", pair), ("src_token_accounts", accounts)):
        monkeypatch.setattr(auditor, n, f)
    monkeypatch.setattr(auditor, "_sleep", nosleep)
    db.upsert_coin({"mint": MINT, "chain": "solana", "name": "Test", "creator": "Dev1",
                    "creator_balance_at_migration": 0, "migrated_at": db.now() - 1800})
    return st


@sync
async def test_freeze_authority_red_at_layer_2(fakes):
    fakes["mint"]["freezeAuthority"] = "Frz1111111111111111111111111111111"
    res = await auditor.handle(scan())
    assert res["passed"] is False
    assert [l["pass"] for l in res["layers"]] == [True, False, None, None, None, None, None, None]
    assert res["layersChecked"] == 2
    assert res["reason"] == "freeze authority set"
    assert all(l["value"] == "not checked" for l in res["layers"][2:])
    rows = db.q("SELECT layer, pass FROM audits WHERE mint=? ORDER BY layer", (MINT,))
    assert [r["pass"] for r in rows] == [1, 0, None, None, None, None, None, None]
    act = db.q("SELECT * FROM activity WHERE mint=?", (MINT,))
    assert act[0]["kind"] == "rejected" and act[0]["subtitle"] == "Auditor · freeze authority set"
    assert act[0]["value"] == "Skipped"
    assert bus.analyst.empty()
    assert db.kv_get(f"audit:{MINT}")["reason"] == "freeze authority set"
    # never re-audit a rejected mint the same night
    fakes["calls"].clear()
    assert await auditor.handle(scan()) is None
    assert fakes["calls"] == []


@sync
async def test_all_pass_goes_to_analyst(fakes):
    fakes["accounts"] = [{"owner": f"W{i}", "tokenAmount": {"amount": "10"}} for i in range(7)]
    res = await auditor.handle(scan())
    assert res["passed"] is True and res["layersChecked"] == 8 and res["reason"] is None
    assert res["holders"] == 7 and res["bundled"] == 0
    msg = bus.analyst.get_nowait()
    assert msg["audit"]["mint"] == MINT and msg["scan"]["pool"] == POOL
    assert db.coin(MINT)["holders"] == 7


@sync
async def test_dev_sold_since_migration(fakes):
    db.upsert_coin({"mint": MINT, "creator_balance_at_migration": 5_000_000_000})
    fakes["bal"] = 1_000_000_000
    res = await auditor.handle(scan())
    assert res["layers"][2]["pass"] is False and res["reason"] == "dev sold since migration"


@sync
async def test_lp_uses_scanner_pool_and_liquidity_drop(fakes):
    fakes["report"]["markets"] = [{"pubkey": "Other", "lp": {"lpLockedPct": 0}},
                                  {"pubkey": POOL, "lp": {"lpLockedPct": 100}}]
    fakes["pair"]["liquidity"]["usd"] = 20000.0  # 50k at scan -> down 60%
    res = await auditor.handle(scan())
    assert res["layers"][4]["pass"] is False and res["reason"] == "liquidity down 60%"


@sync
async def test_no_data_after_retries(fakes):
    setv("auditor.retries", 2)
    fakes["accounts"] = RuntimeError("getProgramAccounts refused")
    res = await auditor.handle(scan())
    assert fakes["calls"].count("gpa") == 3
    assert res["layers"][7] == {"n": 8, "name": "Delegations", "pass": False, "value": "no data: delegations",
                                "source": "rpc"}
    assert res["reason"] == "no data: delegations"


@sync
async def test_audit_off_passes_through(fakes):
    setv("auditor.enabled", False)
    res = await auditor.handle(scan())
    assert res["passed"] is True and res["reason"] == "audit off"
    assert all(l["pass"] is None for l in res["layers"]) and len(res["layers"]) == 8
    assert fakes["calls"] == []
    assert not bus.analyst.empty()


def test_biggest_component_merges_networks():
    g = [{"nodes": [{"id": "a", "participant": True}, {"id": "b", "participant": True},
                    {"id": "c", "participant": True}, {"id": "x", "participant": False}],
          "links": [{"source": "a", "target": "x"}, {"source": "x", "target": "b"}]},
         {"nodes": [{"id": "b", "participant": True}, {"id": "d", "participant": True},
                    {"id": "e", "participant": True}, {"id": "f", "participant": True}],
          "links": [{"source": "b", "target": "d"}, {"source": "e", "target": "f"}]}]
    # a-x-b-d is one component: 3 holders (x is not a holder); c alone; e-f = 2
    assert auditor.biggest_component(g) == 3
    assert auditor.biggest_component([]) == 0
    assert auditor.biggest_component(None) == 0


def test_biggest_component_real_sample():
    g = json.loads((RAW / "rugcheck_insiders_graph.json").read_text())
    n = auditor.biggest_component(g)
    assert 1 <= n <= sum(len(net["nodes"]) for net in g)


def test_delegation_grouping():
    accs = [{"owner": "o1", "delegate": "D1", "tokenAmount": {"amount": "5"}},
            {"owner": "o2", "delegate": "D1", "tokenAmount": {"amount": "7"}},
            {"owner": "o3", "delegate": "D1", "tokenAmount": {"amount": "0"}},   # empty: not a holder account
            {"owner": "o4", "delegate": "D2", "tokenAmount": {"amount": "1"}},
            {"owner": "o5", "tokenAmount": {"amount": "9"}}]
    assert auditor.delegation_groups(accs) == {"D1": 2, "D2": 1}
    assert auditor.holder_count(accs) == 4


@sync
async def test_delegate_over_max_is_red(fakes):
    setv("auditor.max_delegated_accounts", 2)
    fakes["accounts"] = [{"owner": f"o{i}", "delegate": "Drain1111", "tokenAmount": {"amount": "1"}} for i in range(3)]
    res = await auditor.handle(scan())
    assert res["layers"][7]["pass"] is False and res["reason"] == "one delegate on 3 holder accounts"
