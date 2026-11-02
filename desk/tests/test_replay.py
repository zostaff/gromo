from desk import db, replay

R = {"stop_pct": 6, "trail_start_pct": 20, "trail_pct": 12, "timeout_h": 3, "timeout_min_gain_pct": 5, "fee_pct": 1.0}
T0 = 1_000_000.0


def c(i, o, h, l, cl):
    return [T0 + i * 60, o, h, l, cl, 1]


def test_stop_hit():
    s = replay.simulate([c(0, 1, 1.02, 0.99, 1), c(1, 1, 1, 0.5, 0.6), c(2, 0.6, 0.7, 0.3, 0.4)], T0, 6 * 3600, R)
    assert s["sim_exit"] == "stop" and s["sim_pct"] == -8.0     # -6% stop, 2 x 1% fee
    assert s["min_pct"] == -70.0 and s["end_pct"] == -60.0


def test_gap_below_stop_exits_at_open():
    s = replay.simulate([c(0, 1, 1, 1, 1), c(1, 0.5, 0.5, 0.4, 0.45)], T0, 6 * 3600, R)
    assert s["sim_exit"] == "stop" and s["sim_pct"] == -52.0


def test_trailing_stop():
    cs = [c(0, 1, 1, 1, 1), c(1, 1, 2.0, 1, 2.0), c(2, 2.0, 2.0, 1.7, 1.75)]
    s = replay.simulate(cs, T0, 6 * 3600, R)
    assert s["sim_exit"] == "trail" and s["sim_pct"] == 74.0      # 2.0 * 0.88 = 1.76
    assert s["max_pct"] == 100.0


def test_timeout_and_horizon():
    cs = [c(i, 1, 1.01, 0.99, 1.0) for i in range(0, 200)]
    s = replay.simulate(cs, T0, 6 * 3600, R)
    assert s["sim_exit"] == "timeout" and s["sim_pct"] == -2.0
    s = replay.simulate([c(0, 1, 1, 1, 1), c(1, 1, 1.1, 1, 1.1)], T0, 6 * 3600, R)
    assert s["sim_exit"] == "horizon" and s["sim_pct"] == 8.0


def test_no_candles():
    assert replay.simulate([], T0, 3600, R) is None


def test_decisions_and_summary():
    db.use(":memory:")
    replay.ensure_schema()
    now = db.now()
    db.upsert_coin({"mint": "A", "chain": "solana", "symbol": "AAA"})
    db.upsert_coin({"mint": "B", "chain": "solana", "symbol": "BBB"})
    db.insert("scans", {"mint": "A", "ts": now - 3600, "verdict": "kill", "reason": "top 10 hold 90% > 25%"})
    db.insert("activity", {"ts": now - 1800, "kind": "rejected", "mint": "B", "agent": "auditor",
                           "title": "Rejected BBB", "subtitle": "Auditor · dev holds 31%"})
    d = replay.decisions(now - 86400)
    assert d["A"]["stage"] == "scanner" and d["B"]["reason"] == "dev holds 31%"
    replay.save({"mint": "A", "chain": "solana", "stage": "scanner", "reason": "x", "decided_at": now - 3600,
                 "status": "done", "price0": 1, "max_pct": 5, "min_pct": -80, "end_pct": -75, "sim_pct": -8,
                 "sim_exit": "stop", "horizon_h": 6, "evaluated_at": now})
    s = replay.summary(24)
    assert s["summary"]["dodged"] == 1 and s["summary"]["dumped"] == 1 and s["pending"] == 1
    assert s["rows"][0]["outcome"] == "dodged" and s["rows"][0]["coinUrl"]
