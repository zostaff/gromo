"""Auditor: layer order, stop at first red, bundling components, delegation grouping, layer 9. No network."""
from __future__ import annotations

import json
from pathlib import Path

import asyncio
import functools

import pytest

from desk import bus, config, db
from desk import fields as F
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
                     "totalHolders": 500, "creatorTokens": None},
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
    assert [l["pass"] for l in res["layers"]] == [True, False, None, None, None, None, None, None, None]
    assert res["layersChecked"] == 2
    assert res["reason"] == "freeze authority set"
    assert all(l["value"] == "not checked" for l in res["layers"][2:])
    rows = db.q("SELECT layer, pass FROM audits WHERE mint=? ORDER BY layer", (MINT,))
    assert [r["pass"] for r in rows] == [1, 0, None, None, None, None, None, None, None]
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
    assert res["passed"] is True and res["layersChecked"] == 9 and res["reason"] is None
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
    assert all(l["pass"] is None for l in res["layers"]) and len(res["layers"]) == 9
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


# ---- layer 9 -------------------------------------------------------------------------------------
def iso_ago(s):
    return auditor.iso(db.now() - s)


@sync
async def test_sol_dev_history_first_launch_ok(fakes):
    res = await auditor.handle(scan())
    l9 = res["layers"][8]
    assert l9 == {"n": 9, "name": "Dev history", "pass": True, "value": "No previous launches", "source": "rugcheck"}
    assert db.kv_get("auditor_card")["last"] == "9/9 → Executor"


@sync
async def test_sol_dev_history_serial_launcher_red(fakes):
    fakes["report"]["creatorTokens"] = [{"mint": f"M{i}", "marketCap": 90000.0, "createdAt": iso_ago(3600 * (i + 1))}
                                        for i in range(5)]
    res = await auditor.handle(scan())
    assert res["passed"] is False and res["layers"][8]["value"] == "5 launches in 7d"
    assert res["reason"] == "dev launched 5 coins in 7d"


@sync
async def test_sol_dev_history_mostly_dead_red_and_ok(fakes):
    old = 30 * 86400
    fakes["report"]["creatorTokens"] = [{"mint": "A", "marketCap": 120.0, "createdAt": iso_ago(old)},
                                        {"mint": "B", "marketCap": 33.0, "createdAt": iso_ago(old)},
                                        {"mint": "C", "marketCap": 41000.0, "createdAt": iso_ago(old)}]
    res = await auditor.handle(scan())
    assert res["layers"][8]["pass"] is False and res["layers"][8]["value"] == "3 launches · 2 dead"
    assert res["reason"] == "dev: 2 of 3 coins dead"

    db.q("DELETE FROM activity")
    fakes["report"]["creatorTokens"][0]["marketCap"] = 60000.0
    res = await auditor.handle(scan())
    assert res["passed"] is True and res["layers"][8]["value"] == "3 launches · 1 dead"


@sync
async def test_sol_dev_history_young_coins_not_judged(fakes):
    # 2 dead-looking coins younger than a day: too early to call them dead
    fakes["report"]["creatorTokens"] = [{"mint": "A", "marketCap": 100.0, "createdAt": iso_ago(600)},
                                        {"mint": "B", "marketCap": 100.0, "createdAt": iso_ago(900)}]
    res = await auditor.handle(scan())
    assert res["passed"] is True and res["layers"][8]["value"] == "2 launches · 0 dead"


@sync
async def test_sol_dev_history_missing_key_is_no_data(fakes):
    del fakes["report"]["creatorTokens"]
    res = await auditor.handle(scan())
    assert res["layers"][8]["pass"] is False and res["reason"] == "no data: dev history"


@sync
async def test_layer9_toggle_off(fakes):
    setv("auditor.layer9", False)
    del fakes["report"]["creatorTokens"]
    res = await auditor.handle(scan())
    assert res["passed"] is True and len(res["layers"]) == 8 and res["layersChecked"] == 8
    assert auditor.layer_count("robinhood") == 8


def test_dev_history_real_samples():
    now = 1793624400.0  # 2026-11-02 13:00 UTC, when the samples were saved
    rep = json.loads((RAW / "rugcheck_report_creator_tokens.json").read_text())
    h = auditor.dev_history(F.rc_creator_tokens(rep), now)
    assert h["total"] == 50 and h["last7d"] == 20 and h["judged"] == 48 and h["dead"] == 42
    old = json.loads((RAW / "rugcheck_report_creator_tokens_old.json").read_text())
    h = auditor.dev_history(F.rc_creator_tokens(old), now)
    assert h["total"] == 44 and h["last7d"] == 0 and h["dead"] == 40
    fresh = json.loads((RAW / "rugcheck_report.json").read_text())
    assert F.rc_has_key(fresh, "creatorTokens") and F.rc_creator_tokens(fresh) == []


RH = "0x6511c233790ec10f7d673c64dc82c3f941d57828"


@pytest.fixture
def rh(monkeypatch):
    st = {"x": json.loads((RAW / "grokhood_token_x_activity.json").read_text()), "calls": 0}

    async def ok(c):
        return (True, "fine", "grokhood", None)

    async def gh(kind, addr):
        assert kind == "x"
        st["calls"] += 1
        return st["x"]

    async def nosleep(s):
        return None

    monkeypatch.setattr(auditor, "RH_CHECKS", [ok] * 8)
    monkeypatch.setattr(auditor, "src_gh", gh)
    monkeypatch.setattr(auditor, "_sleep", nosleep)
    db.upsert_coin({"mint": RH, "chain": "robinhood", "name": "Oak"})
    return st


def rh_scan():
    return scan(chain="robinhood", mint=RH, name="Oak", pool=None)


@sync
async def test_rh_x_activity_organic_real_sample(rh):
    res = await auditor.handle(rh_scan())
    assert res["passed"] is True and res["layersChecked"] == 9
    assert res["layers"][8] == {"n": 9, "name": "X activity", "pass": True, "value": "Organic · 1 post",
                                "source": "grokhood"}


@sync
async def test_rh_x_activity_spam_red(rh):
    rh["x"].update({"level": "spam", "score": 22})
    rh["x"]["counts"].update({"posts": 31, "authors": 9})
    res = await auditor.handle(rh_scan())
    assert res["passed"] is False and res["layers"][8]["value"] == "Coordinated shill · 9 accounts"
    assert res["reason"] == "coordinated shill on X"


@sync
async def test_rh_x_activity_mixed_follows_setting(rh):
    rh["x"].update({"level": "mixed", "score": 55})
    rh["x"]["counts"].update({"posts": 14, "authors": 6})
    res = await auditor.handle(rh_scan())
    assert res["passed"] is True and res["layers"][8]["value"] == "Mixed · 14 posts"
    setv("auditor.max_shill_level", "organic")
    res = await auditor.audit(rh_scan())
    assert res["passed"] is False and res["layers"][8]["value"] == "Mixed shill · 6 accounts"


@sync
async def test_rh_x_activity_no_posts_ok(rh):
    rh["x"] = json.loads((RAW / "grokhood_token_x_activity_noposts.json").read_text())
    res = await auditor.handle(rh_scan())
    assert res["passed"] is True and res["layers"][8]["value"] == "No X posts"


@sync
async def test_rh_x_activity_untracked_and_404_no_data(rh):
    setv("auditor.retries", 1)
    rh["x"] = json.loads((RAW / "grokhood_token_x_activity_untracked.json").read_text())
    res = await auditor.handle(rh_scan())
    # untracked is a known state from GROKHOOD, not a failed read: neutral pass
    assert res["layers"][8]["value"] == "Not tracked on X" and res["layers"][8]["pass"] is True
    db.q("DELETE FROM activity")
    rh["x"] = None  # 404: request_json returns None
    res = await auditor.handle(rh_scan())
    assert res["passed"] is False and res["reason"] == "no data: x activity"
