"""Copy feed, night post, replay route."""
import sqlite3

from fastapi.testclient import TestClient

from desk import copyfeed, db, nightpost, replay


def _copydb(path, token=True):
    c = sqlite3.connect(path)
    c.executescript("""
CREATE TABLE traders (id TEXT PRIMARY KEY, handle TEXT, name TEXT, added_at REAL, initialized INTEGER, last_poll REAL);
CREATE TABLE trades (swap_id TEXT PRIMARY KEY, trader_id TEXT, handle TEXT, ts REAL, side TEXT, address TEXT,
  chain TEXT, symbol TEXT, name TEXT, usd REAL, mcap REAL, liq REAL, sent INTEGER);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);""")
    now = db.now()
    if token:
        c.execute("INSERT INTO kv VALUES ('fomo_token', '\"abc\"')")
    for i, h in enumerate(("alpha", "beta", "gamma")):
        c.execute("INSERT INTO traders VALUES (?,?,?,?,1,?)", (f"t{i}", h, h, now, now - 30))
    rows = [("s1", "t0", "alpha", now - 60, "buy", "MintAAA", "solana", "AAA", 120.0),
            ("s2", "t1", "beta", now - 50, "buy", "minTaaa", "solana", "AAA", None),
            ("s3", "t2", "gamma", now - 40, "buy", "MintBBB", "solana", "BBB", 80.0),
            ("s4", "t0", "alpha", now - 30, "sell", "MintCCC", "solana", "CCC", 50.0)]
    for r in rows:
        c.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,1)", (*r[:8], r[7], r[8]))
    c.commit()
    c.close()


def test_copy_feed_states_and_desk_join(tmp_path, monkeypatch):
    monkeypatch.delenv("FOMO_TOKEN", raising=False)
    monkeypatch.setattr(copyfeed, "COPY_DB", tmp_path / "none.db")
    assert copyfeed.feed()["state"] == "no_token"
    p = tmp_path / "copybot.db"
    _copydb(p)
    monkeypatch.setattr(copyfeed, "COPY_DB", p)
    db.upsert_coin({"mint": "MintAAA", "chain": "solana", "symbol": "AAA"})
    db.insert("activity", {"ts": db.now(), "kind": "rejected", "mint": "MintAAA", "agent": "auditor",
                           "title": "Rejected AAA", "subtitle": "Auditor · dev holds 31%"})
    f = copyfeed.feed()
    assert f["state"] == "running" and len(f["traders"]) == 3 and len(f["trades"]) == 4
    assert len(f["hot"]) == 1                                    # AAA: alpha + beta, case-insensitive
    hot = f["hot"][0]
    assert hot["buyers"] == 2 and hot["usd"] == 120.0
    assert hot["desk"] == {"verdict": "rejected", "label": "Auditor · dev holds 31%"}
    assert "fomo.family" in hot["coinUrl"]
    bbb = next(t for t in f["trades"] if t["symbol"] == "BBB")
    assert bbb["desk"]["verdict"] == "unseen"


def test_copy_feed_no_token(tmp_path, monkeypatch):
    monkeypatch.delenv("FOMO_TOKEN", raising=False)
    p = tmp_path / "copybot.db"
    _copydb(p, token=False)
    monkeypatch.setattr(copyfeed, "COPY_DB", p)
    assert copyfeed.feed()["state"] == "no_token"


def test_night_post_text_and_card(tmp_path):
    replay.ensure_schema()
    now = db.now()
    db.start_night(200.0, "PAPER")
    db.upsert_coin({"mint": "M1", "chain": "solana", "symbol": "ONE", "first_seen": now - 100})
    db.insert("scans", {"mint": "M1", "ts": now - 50, "verdict": "kill", "reason": "top 10"})
    p = nightpost.build(now - 3600, "2026-11-02", tmp_path)
    assert "watched 1 new coins" in p["text"] and "No trades tonight" in p["text"]
    assert "—" not in p["text"] and "–" not in p["text"]
    assert len(p["text"]) <= 1024
    assert p["png"][:8] == b"\x89PNG\r\n\x1a\n" and (tmp_path / "2026-11-02-card.png").is_file()
    assert nightpost.latest(tmp_path)["date"] == "2026-11-02"


def test_round3_routes(monkeypatch, tmp_path):
    from desk import api, report
    monkeypatch.setenv("APP_TOKEN", "tok12345")
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(copyfeed, "COPY_DB", tmp_path / "none.db")
    c = TestClient(api.app)
    h = {"Authorization": "Bearer tok12345"}
    assert c.get("/api/copy", headers=h).json()["state"] == "no_token"
    r = c.get("/api/replay?h=24", headers=h).json()
    assert r["windowH"] == 24 and r["summary"]["refused"] == 0
    assert c.get("/api/night-post", headers=h).json() == {"date": None, "text": None, "cardUrl": None}
    db.start_night(200.0, "PAPER")
    pv = c.post("/api/night-post/preview", headers=h).json()
    assert pv["text"] and pv["cardUrl"].startswith("/reports/preview-card.png?t=")
    img = c.get(pv["cardUrl"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert c.get("/reports/preview-card.png").status_code == 401
