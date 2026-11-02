import asyncio

from desk import config, db
from desk.agents import executor
from desk.tests.conftest import setv


def test_stack_200_bumps_to_min_ticket():
    size, why = executor.size_ticket(200, 200, 1_000_000)
    assert why is None and size == 47.50  # 8% of 200 = $16 -> bumped


def test_stack_200_skip_mode_skips():
    setv("executor.below_min", "skip")
    size, why = executor.size_ticket(200, 200, 1_000_000)
    assert size is None and why == "under $47.50"


def test_bump_needs_free_stack():
    size, why = executor.size_ticket(200, 40, 1_000_000)
    assert size is None and why == "under $47.50"


def test_ticket_check_warning_at_200_with_skip():
    setv("executor.below_min", "skip")
    t = config.ticket_check(200)
    assert t["ok"] is False and "skipped" in t["message"] and t["min_stack"] == 593.75


def test_liquidity_40000_caps_at_800():
    size, why = executor.size_ticket(100_000, 100_000, 40_000)
    assert why is None and size == 800


def test_no_free_stack():
    size, why = executor.size_ticket(10_000, 500, None)
    assert size is None and why == "no free stack"


def _msg(mint="Q", liq=40_000, price=0.01):
    return {"scan": {"chain": "solana", "mint": mint, "name": "Quux", "symbol": "QX", "liquidityUsd": liq},
            "audit": {"layers": [{"n": i, "pass": True} for i in range(1, 9)]},
            "verdict": {"mint": mint, "score": 74}, "market": {"priceUsd": price}}


def test_paper_signal_push_and_fill(fake_market, pushes, monkeypatch):
    monkeypatch.setenv("FOMO_REF", "ref1")
    setv("desk.paper_start_stack", 1000)
    fake_market.set("Q", priceUsd=0.0105, liquidityUsd=39_000)
    r = asyncio.run(executor.handle(_msg(), paper_bar=False))
    assert r["status"] == "bought" and r["size"] == 80
    p = pushes[0]
    assert p["text"] == ("PAPER · Buy Quux · $80.00 · 8% of stack\n"
                         "Auditor 8/8 · Analyst 74 · stop -6% · trail 12%")
    assert p["buttons"][0][0] == "Buy on FOMO" and "r=ref1" in p["buttons"][0][1]
    assert p["buttons"][1] == ("Bought", f"cb:bought:{r['signal_id']}")
    assert p["buttons"][2] == ("Skip", f"cb:skip:{r['signal_id']}")
    pos = db.q1("SELECT * FROM positions")
    assert pos["entry_price"] == 0.0105 and pos["stop_pct"] == 6 and abs(pos["stop_price"] - 0.0105 * 0.94) < 1e-12
    assert db.q1("SELECT * FROM activity WHERE kind='bought'")["value"] == "$80.00"
    assert db.kv_get("executor_card")["state"] == "filled"
    # one push per mint per night
    r2 = asyncio.run(executor.handle(_msg(), paper_bar=False))
    assert r2["status"] == "dup" and len(pushes) == 1


def test_recheck_skips(fake_market, pushes):
    fake_market.set("L", priceUsd=0.01, liquidityUsd=30_000)  # 25% down from 40k
    assert asyncio.run(executor.handle(_msg("L"), paper_bar=False))["reason"] == "liquidity dropping"
    fake_market.set("R", priceUsd=0.0116, liquidityUsd=40_000)  # +16% since analyst
    assert asyncio.run(executor.handle(_msg("R"), paper_bar=False))["reason"] == "ran before the push"
    fake_market.set("P", priceUsd=None, liquidityUsd=40_000)
    assert asyncio.run(executor.handle(_msg("P"), paper_bar=False))["reason"] == "no price"
    assert pushes == []


def test_live_waits_for_bought_then_skip(fake_market, pushes):
    from desk import actions
    setv("desk.mode", "LIVE")
    setv("desk.live_stack", 1000)
    fake_market.set("Q", priceUsd=0.01, liquidityUsd=40_000)

    async def go():
        r = await executor.handle(_msg(), paper_bar=False)
        assert r["status"] == "sent" and db.q1("SELECT COUNT(*) AS n FROM positions")["n"] == 0
        assert (await actions.bought(r["signal_id"]))["ok"]
        for t in list(executor._watchers):
            t.cancel()
        return r
    r = asyncio.run(go())
    assert db.q1("SELECT * FROM positions")["mode"] == "LIVE"
    assert (asyncio.run(actions.skip(r["signal_id"])))["ok"]
    assert db.q1("SELECT * FROM skips WHERE mint='Q'")["until"] > db.now()
