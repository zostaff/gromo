"""API auth, /api/state shape on an empty db, settings validation, secrets not leaked."""
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk import config, db

TOKEN = "test-token-123456789"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("APP_TOKEN", TOKEN)
    db.use(":memory:")
    config.load({})
    from desk import api
    return TestClient(api.app)


H = {"Authorization": f"Bearer {TOKEN}"}


def test_401_without_token(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(f"/api/state?t={TOKEN}").status_code == 401  # ?t= only for SSE
    assert client.get("/api/settings").status_code == 401
    assert client.get("/reports/2026-01-01.html").status_code == 401


def test_401_when_no_app_token_configured(client, monkeypatch):
    monkeypatch.delenv("APP_TOKEN")
    assert client.get("/api/state", headers=H).status_code == 401


def test_state_shape_empty_db(client):
    r = client.get("/api/state", headers=H)
    assert r.status_code == 200
    s = r.json()
    for k, t in {"balance": float, "todayPct": float, "multiplier": float, "mode": str, "nightStart": float,
                 "online": bool, "updatedAt": float, "unread": int, "handle": str, "agents": list}.items():
        assert isinstance(s[k], (t, int) if t is float else t), k
    assert s["mode"] == "PAPER" and s["online"] is True
    assert [a["name"] for a in s["agents"]] == ["scanner", "auditor", "analyst", "executor", "risk"]
    for a in s["agents"]:
        assert a["status"] == "down"  # no heartbeats yet
        assert set(a) == {"name", "status", "count", "subtitle", "card"}
    cards = {a["name"]: a["card"] for a in s["agents"]}
    assert set(cards["scanner"]) == {"pools", "feed"}
    assert set(cards["auditor"]) == {"takenApart", "rejected", "current", "last"}
    assert set(cards["analyst"]) == {"scoring", "scored", "mode"}
    assert set(cards["executor"]) == {"trades", "lastSize", "stackPct", "state", "progress", "name"}
    assert set(cards["risk"]) == {"open", "guarding", "last", "stopPct", "trailPct"}


def test_other_shapes_empty_db(client):
    c = client.get("/api/chart?range=1H", headers=H).json()
    assert set(c) == {"points", "start", "marks"}
    assert client.get("/api/chart?range=nope", headers=H).status_code == 400
    assert client.get("/api/activity", headers=H).json() == {"rows": [], "next": None}
    assert client.get("/api/audit/abc", headers=H).json() == {"layers": []}
    assert client.get("/api/coin/abc", headers=H).status_code == 404
    acc = client.get("/api/account", headers=H).json()
    assert set(acc) == {"mode", "stack", "handle", "profileUrl", "signupUrl", "telegram", "webpush",
                        "vapidPublicKey", "judge", "grokToday"}
    assert client.get("/api/agents/scanner/decisions", headers=H).json() == {"rows": []}
    assert client.get("/api/search?q=x", headers=H).json() == {"rows": []}


def test_heartbeat_and_pause_status(client):
    db.beat("scanner")
    db.set_paused("auditor", True)
    agents = {a["name"]: a["status"] for a in client.get("/api/state", headers=H).json()["agents"]}
    assert agents["scanner"] == "running" and agents["auditor"] == "paused"


def test_settings_post_validation(client):
    ok = client.post("/api/settings", headers=H, json={"key": "risk.stop_pct", "value": 8})
    assert ok.status_code == 200 and ok.json() == {"key": "risk.stop_pct", "old": 6, "new": 8}
    assert client.post("/api/settings", headers=H, json={"key": "risk.stop_pct", "value": 500}).status_code == 400
    assert client.post("/api/settings", headers=H, json={"key": "nope", "value": 1}).status_code == 400
    assert client.post("/api/settings", headers=H, json={"key": "risk.stop_pct"}).status_code == 400
    bad = client.post("/api/settings", headers=H, json={"key": "desk.mode", "value": "LIVE"})
    assert bad.status_code == 400 and "error" in bad.json()


def test_mode_live_needs_confirm_and_paper_night(client):
    assert client.post("/api/mode", headers=H, json={"mode": "LIVE"}).status_code == 400
    assert client.post("/api/mode", headers=H, json={"mode": "LIVE", "confirm": "LIVE"}).status_code == 400


def test_candles_volume_is_positive_change():
    db.use(":memory:")
    from desk import api
    for ts, p, v in [(60, 1.0, 100), (70, 2.0, 150), (130, 1.5, 120), (140, 1.6, 200)]:
        db.add_price("m", p, v, ts)
    assert api.candles("m", 0) == [[60, 1.0, 2.0, 1.0, 2.0, 50.0], [120, 1.5, 1.6, 1.5, 1.6, 80.0]]


def test_no_secrets_in_web_docs_readme():
    vals = [os.environ.get(k) for k in ("APP_TOKEN", "TG_TOKEN", "XAI_API_KEY")]
    env = ROOT / ".env"
    if env.exists():
        from desk.main import parse_env
        e = parse_env(env.read_text())
        vals += [e.get(k) for k in ("APP_TOKEN", "TG_TOKEN", "XAI_API_KEY")]
    vals = {v for v in vals if v and len(v) >= 8 and v != TOKEN}
    files = [p for d in ("desk/web", "docs") for p in (ROOT / d).rglob("*") if p.is_file()]
    files += [p for p in ROOT.glob("README*") if p.is_file()]
    for p in files:
        if p.name.startswith("._"):
            continue
        text = p.read_text(errors="ignore")
        for v in vals:
            assert v not in text, f"secret found in {p}"


def test_no_dashes_in_user_strings():
    for name in ("api.py", "notify.py", "telegram.py", "report.py", "main.py"):
        assert not re.search("[–—]", (ROOT / "desk" / name).read_text()), name


def test_public_live_no_token_no_secrets(client, monkeypatch):
    from solders.keypair import Keypair
    from desk import api, wallet
    kp = Keypair()
    monkeypatch.setenv("WALLET_PRIVATE_KEY", str(kp))
    monkeypatch.setattr(wallet, "_kp", None)
    api._pub_cache = None
    db.start_night(200, "PAPER")
    assert client.get("/live").status_code == 200
    r = client.get("/public/live.json")
    assert r.status_code == 200
    body = r.text
    d = r.json()
    assert d["mode"] == "PAPER" and d["wallet"] is None and d["auto"] is False
    assert {"balance", "funnel", "chart", "activity", "agents", "replay", "feed"} <= set(d)
    assert TOKEN not in body and str(kp) not in body
    # LIVE + auto: the wallet address (never the key) is public as proof
    config._cache["desk.mode"] = "LIVE"
    config._cache["trade.auto"] = True
    api._pub_cache = None
    d = client.get("/public/live.json").json()
    assert d["wallet"] == str(kp.pubkey()) and d["auto"] is True and str(kp) not in str(d)
    config._cache["desk.public_page"] = False
    api._pub_cache = None
    assert client.get("/public/live.json").status_code == 404 and client.get("/live").status_code == 404
