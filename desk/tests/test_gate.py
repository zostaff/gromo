import asyncio

import pytest

from desk import bus, db
from desk.agents import analyst
from desk.tests.conftest import setv

AT = {"alive_6h": 0.70, "organic": 0.65, "flush": 0.75, "chase": 0.30}


def test_exactly_at_threshold_passes():
    score, passed, failed = analyst.gate(dict(AT))
    assert score == 70 and passed and failed == []


@pytest.mark.parametrize("answers,label", [
    ({"alive_6h": 0.69, "organic": 0.9, "flush": 1, "chase": 0.0}, "alive 0.69"),
    ({"alive_6h": 0.9, "organic": 0.64, "flush": 1, "chase": 0.0}, "organic 0.64"),
    ({"alive_6h": 0.9, "organic": 0.9, "flush": 1, "chase": 0.31}, "chase 0.31"),
    ({"alive_6h": 0.9, "organic": 0.9, "flush": 0.06, "chase": 0.1}, "score 69"),
])
def test_each_rejects(answers, label):
    score, passed, failed = analyst.gate(answers)
    assert not passed
    assert failed == [label]


def _msg(mint, bundled=None, chain="solana"):
    return {"scan": {"agent": "scanner", "chain": chain, "mint": mint, "name": f"C{mint}", "symbol": "C",
                     "liquidityUsd": 50000, "top10Pct": 12.0, "ageMin": 20},
            "audit": {"agent": "auditor", "mint": mint, "chain": chain, "passed": True, "layersChecked": 8,
                      "layers": [], "holders": 400, "bundled": bundled, "safetyScore": None}}


def _rules_numbers(fm, mint, vol_h1=17500, buys_h1=97.5, chg_h1=24, chg_m5=-6, buys_m5=10, sells_m5=5):
    fm.set(mint, priceUsd=0.001, liquidityUsd=50000, vol_m5=1000, vol_h1=vol_h1, buys_h1=buys_h1,
           sells_h1=50, chg_m5=chg_m5, chg_h1=chg_h1, buys_m5=buys_m5, sells_m5=sells_m5)


def test_rules_at_threshold_passes(fake_market):
    _rules_numbers(fake_market, "A")
    v = asyncio.run(analyst.analyze(_msg("A")))
    assert v["passed"], v
    assert v["model"] == "rules" and v["inputTokens"] == 0
    assert v["answers"] == {"alive_6h": 0.7, "organic": 0.65, "flush": 1.0, "chase": 0.3}
    assert bus.executor.get_nowait()["verdict"]["mint"] == "A"


@pytest.mark.parametrize("kw,label", [
    ({"vol_h1": 17250}, "alive 0.69"),
    ({"buys_h1": 96}, "organic 0.64"),
    ({"chg_h1": 24.8}, "chase 0.31"),
])
def test_rules_rejects_read_analyst_rules(fake_market, kw, label):
    _rules_numbers(fake_market, "B", **kw)
    v = asyncio.run(analyst.analyze(_msg("B")))
    assert not v["passed"] and label in v["failed"]
    row = db.q1("SELECT * FROM activity WHERE kind='rejected'")
    assert row["title"] == "Rejected CB"
    assert row["subtitle"].startswith("Analyst (rules) · ") and label in row["subtitle"]
    assert bus.executor.empty()


def test_rules_score_69_rejects(fake_market):
    # alive 1, organic 1, flush 0 (chg_m5 >= 0), chase 0.76 -> chase fails too; use chase-free combo:
    # alive 0.76, organic 1, flush 0, chase 0 -> score 69
    setv("analyst.max_chase", 1)
    _rules_numbers(fake_market, "S", vol_h1=19000, buys_h1=200, chg_h1=0, chg_m5=1)
    v = asyncio.run(analyst.analyze(_msg("S")))
    assert v["score"] == 69 and v["failed"] == ["score 69"]
    assert db.q1("SELECT subtitle FROM activity")["subtitle"] == "Analyst (rules) · score 69"


def test_rules_bundled_penalty(fake_market):
    _rules_numbers(fake_market, "D", buys_h1=150)
    v = asyncio.run(analyst.analyze(_msg("D", bundled=3)))
    assert v["answers"]["organic"] == 0.5 and "organic 0.50" in v["failed"]


def test_rules_missing_number_is_no_data(fake_market):
    fake_market.set("E", priceUsd=1.0, vol_h1=None)
    v = asyncio.run(analyst.analyze(_msg("E")))
    assert not v["passed"] and v["reason"] == "no data"
    assert db.q1("SELECT subtitle FROM activity")["subtitle"] == "Analyst (rules) · no data"


def test_never_reask_same_mint(fake_market):
    _rules_numbers(fake_market, "F")
    assert asyncio.run(analyst.analyze(_msg("F"))) is not None
    analyst._asked.clear()  # even after a restart, the db remembers
    assert asyncio.run(analyst.analyze(_msg("F"))) is None
    assert db.q1("SELECT COUNT(*) AS n FROM verdicts")["n"] == 1


def test_grok_without_key_rejects_not_rules(fake_market):
    setv("analyst.mode", "grok")
    _rules_numbers(fake_market, "G")
    v = asyncio.run(analyst.analyze(_msg("G")))
    assert not v["passed"] and v["reason"] == "no key" and v["model"] != "rules"
    assert db.q1("SELECT subtitle FROM activity")["subtitle"] == "Analyst · no key"


def test_grok_budget_and_bad_answer(fake_market, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test")
    setv("analyst.mode", "grok")
    setv("analyst.grok_daily_usd", 0)
    _rules_numbers(fake_market, "H")
    v = asyncio.run(analyst.analyze(_msg("H")))
    assert v["reason"] == "grok budget"

    setv("analyst.grok_daily_usd", 1)

    async def fake_post(client, body, key):
        assert body["response_format"]["json_schema"]["strict"] is True
        return {"model": body["model"], "usage": {"prompt_tokens": 500, "completion_tokens": 40},
                "choices": [{"message": {"content": '{"alive_6h":1.2,"organic":0.5,"flush":0.1,"chase":0.1}'}}]}
    monkeypatch.setattr(analyst, "_post", fake_post)
    _rules_numbers(fake_market, "I")
    v = asyncio.run(analyst.analyze(_msg("I"), client=object()))
    assert v["reason"] == "bad answer"
    assert analyst.grok_today()["calls"] == 1 and analyst.grok_today()["costUsd"] > 0


def test_grok_retry_once_then_no_answer(fake_market, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test")
    setv("analyst.mode", "grok")
    calls = []

    async def boom(client, body, key):
        calls.append(1)
        raise TimeoutError

    async def nosleep(_):
        return None
    monkeypatch.setattr(analyst, "_post", boom)
    monkeypatch.setattr(analyst.asyncio, "sleep", nosleep)
    _rules_numbers(fake_market, "J")
    v = asyncio.run(analyst.analyze(_msg("J"), client=object()))
    assert v["reason"] == "no answer" and len(calls) == 2


def test_grok_pass_and_state_text(fake_market, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test")
    setv("analyst.mode", "grok")
    seen = {}

    async def ok(client, body, key):
        seen["state"] = body["messages"][1]["content"]
        return {"model": body["model"], "usage": {"prompt_tokens": 500, "completion_tokens": 40},
                "choices": [{"message": {"content": '{"alive_6h":0.8,"organic":0.8,"flush":0.5,"chase":0.1}'}}]}
    monkeypatch.setattr(analyst, "_post", ok)
    _rules_numbers(fake_market, "0xK")
    msg = _msg("0xK", chain="robinhood")
    msg["audit"]["safetyScore"] = 82
    msg["audit"]["layers"] = [{"n": 2, "name": "Rings", "pass": True, "value": "none"}]
    v = asyncio.run(analyst.analyze(msg, client=object()))
    assert v["passed"] and v["inputTokens"] == 500
    st = seen["state"]
    assert "buy" not in st.lower() and "balance" not in st.lower() and len(st) <= 3000
    assert "Safety Score" in st and "82" in st and "Rings: none" in st
