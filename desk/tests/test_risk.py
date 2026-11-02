import asyncio
import time

from desk import config, db
from desk.agents import executor, risk
from desk.tests.conftest import setv


def _open(mint="M", entry=1.0, size=100.0, mode="PAPER", opened_at=None):
    db.upsert_coin({"mint": mint, "chain": "solana", "name": f"N{mint}", "symbol": "N"})
    sid = db.insert("signals", {"mint": mint, "ts": db.now(), "price": entry, "size_usd": size,
                                "stack_pct": 8.0, "link": "x", "mode": mode, "status": "sent"})
    pid = executor.open_position(db.q1("SELECT * FROM signals WHERE id=?", (sid,)))
    if opened_at is not None:
        db.update("positions", pid, {"opened_at": opened_at})
    return pid


def _pos(pid):
    return db.q1("SELECT * FROM positions WHERE id=?", (pid,))


def _poll(fm, mint, price, liq=50000.0, now=None):
    fm.set(mint, priceUsd=price, liquidityUsd=liq)
    asyncio.run(risk.poll_once(now))


def test_trail_after_plus_25_then_13_off_top(fake_market, pushes):
    pid = _open()
    _poll(fake_market, "M", 1.25)
    p = _pos(pid)
    assert p["trail_active"] == 1 and p["peak_price"] == 1.25 and p["status"] == "open"
    _poll(fake_market, "M", 1.25 * 0.87)
    p = _pos(pid)
    assert p["status"] == "closed" and p["exit_reason"] == "trail"
    assert abs(p["exit_price"] - 1.0875) < 1e-9
    act = db.q1("SELECT * FROM activity WHERE kind='sold'")
    assert act["title"] == "Sold NM" and act["subtitle"] == "Risk · trailing stop"
    assert pushes[-1]["buttons"][0][0] == "Sell on FOMO" and pushes[-1]["buttons"][1] == ("Sold", f"cb:sold:{pid}")


def test_trail_never_turns_off(fake_market, pushes):
    pid = _open()
    _poll(fake_market, "M", 1.21)
    _poll(fake_market, "M", 1.10)  # back under +20%, 9% off top: still active, no exit
    p = _pos(pid)
    assert p["trail_active"] == 1 and p["status"] == "open"


def test_stop_at_minus_6_1(fake_market, pushes):
    pid = _open(size=100)
    _poll(fake_market, "M", 0.939)
    p = _pos(pid)
    assert p["status"] == "closed" and p["exit_reason"] == "stop"
    act = db.q1("SELECT * FROM activity WHERE kind='stopped'")
    assert act["title"] == "Stopped NM" and act["subtitle"] == "Risk · -6% stop"
    assert act["value2"] == "-6.1%"
    # pnl minus fees (100 in, 93.9 out, flat $0.95 each side, 10% ref discount)
    assert p["pnl_usd"] == round(93.9 - 100 - 2 * 0.95 * 0.9, 2)
    assert act["value"] == f"-${abs(p['pnl_usd']):,.2f}"


def test_minus_5_9_is_not_stop(fake_market, pushes):
    pid = _open()
    _poll(fake_market, "M", 0.941)
    assert _pos(pid)["status"] == "open"


def test_timeout_3h_at_plus_4(fake_market, pushes):
    t0 = time.time()
    pid = _open(opened_at=t0 - 3 * 3600 - 1)
    _poll(fake_market, "M", 1.04, now=t0)
    p = _pos(pid)
    assert p["status"] == "closed" and p["exit_reason"] == "timeout"
    act = db.q1("SELECT * FROM activity WHERE kind='timeout'")
    assert act["title"] == "Sold NM" and act["subtitle"] == "Risk · dead money"


def test_no_timeout_above_min_gain(fake_market, pushes):
    t0 = time.time()
    pid = _open(opened_at=t0 - 4 * 3600)
    _poll(fake_market, "M", 1.06, now=t0)
    assert _pos(pid)["status"] == "open"


def test_pulled_liquidity_zero(fake_market, pushes):
    pid = _open()
    _poll(fake_market, "M", 0.99, liq=0)
    assert _pos(pid)["exit_reason"] == "pulled"


def test_no_price_four_polls_pushes_once(fake_market, pushes):
    pid = _open()
    for _ in range(5):
        _poll(fake_market, "M", None)
    warns = [p for p in pushes if p["kind"] == "warn"]
    assert len(warns) == 1 and warns[0]["text"] == "NM: no price for 1 min"
    assert _pos(pid)["status"] == "open"


def test_loosening_stop_never_moves_open_stop_down(fake_market, pushes):
    pid = _open()
    before = _pos(pid)["stop_price"]
    assert risk.apply_stop_setting(10) == 0
    assert _pos(pid)["stop_price"] == before and _pos(pid)["stop_pct"] == 6
    assert risk.apply_stop_setting(4) == 1  # tighter applies
    assert abs(_pos(pid)["stop_price"] - 0.96) < 1e-9 and _pos(pid)["stop_pct"] == 4


def test_set_stop_8_new_position_old_still_6(fake_market, pushes):
    asyncio.run(risk.poll_once())  # records the current stop setting
    old = _open("OLD")
    setv("risk.stop_pct", 8)
    asyncio.run(risk.poll_once())  # settings watcher runs, looser stop is ignored for OLD
    new = _open("NEW")
    assert _pos(old)["stop_pct"] == 6 and _pos(new)["stop_pct"] == 8
    fake_market.set("OLD", priceUsd=0.95, liquidityUsd=5e4)
    fake_market.set("NEW", priceUsd=0.919, liquidityUsd=5e4)
    asyncio.run(risk.poll_once())
    assert _pos(new)["exit_reason"] == "stop" and _pos(old)["status"] == "open"
    act = db.q1("SELECT * FROM activity WHERE kind='stopped' AND mint='NEW'")
    assert "-8%" in act["subtitle"] and act["value2"] == "-8.1%"
    fake_market.set("OLD", priceUsd=0.939, liquidityUsd=5e4)
    asyncio.run(risk.poll_once())
    act = db.q1("SELECT * FROM activity WHERE kind='stopped' AND mint='OLD'")
    assert act["subtitle"] == "Risk · -6% stop"


def test_live_exit_waits_for_sold(fake_market, pushes):
    from desk import actions
    pid = _open(mode="LIVE")
    _poll(fake_market, "M", 0.93)
    assert _pos(pid)["status"] == "exiting"
    _poll(fake_market, "M", 0.80)  # later price does not change the exit price
    r = asyncio.run(actions.sold(pid))
    p = _pos(pid)
    assert r["ok"] and p["status"] == "closed" and p["exit_price"] == 0.93


def test_one_batched_read_per_chain(fake_market, pushes):
    _open("A"); _open("B")
    asyncio.run(risk.poll_once())
    assert fake_market.calls == [("solana", ["A", "B"])]
    assert db.q1("SELECT COUNT(*) AS n FROM prices")["n"] == 0  # no price -> nothing recorded
