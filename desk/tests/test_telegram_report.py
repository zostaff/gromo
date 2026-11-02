"""Telegram owner lock + commands, notify PAPER prefix, morning report (no network: sends are captured)."""
import asyncio

import pytest

from desk import config, db, links, notify, report, telegram


@pytest.fixture()
def sent(monkeypatch, tmp_path):
    db.use(":memory:")
    config.load({})
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
    out = []

    async def fake_send(chat, text, buttons=None, image=None, is_html=False):
        out.append({"chat": chat, "text": text, "buttons": buttons or []})
        return {}

    async def fake_webpush(body, url):
        return 0
    monkeypatch.setattr(notify, "tg_send", fake_send)
    monkeypatch.setattr(notify, "webpush_all", fake_webpush)
    monkeypatch.setenv("TG_TOKEN", "x" * 20)
    return out


def msg(chat, text, kind="private"):
    return {"update_id": 1, "message": {"message_id": 5, "chat": {"id": chat, "type": kind}, "text": text}}


def run(c):
    return asyncio.run(c)


def test_owner_lock(sent):
    run(telegram.handle_update(msg(7, "hello")))
    assert sent == [] and db.kv_get("owner_chat_id") is None
    run(telegram.handle_update(msg(7, "/start")))
    assert db.kv_get("owner_chat_id") == 7
    run(telegram.handle_update(msg(8, "/start")))
    run(telegram.handle_update(msg(8, "status")))
    assert all(m["chat"] == 7 for m in sent)


def test_set_stop_and_pause_rules(sent):
    db.kv_set("owner_chat_id", 7)
    run(telegram.handle_text(7, "set stop to 8"))
    assert sent[-1]["text"] == "stop -6% -> -8%"
    assert config.get("risk.stop_pct") == 8
    db.insert("positions", {"mint": "m", "opened_at": db.now(), "entry_price": 1, "size_usd": 10, "status": "open",
                            "mode": "PAPER"})
    run(telegram.handle_text(7, "pause risk"))
    assert "cannot pause" in sent[-1]["text"] and not db.is_paused("risk")
    run(telegram.handle_text(7, "/pause scanner"))
    assert db.is_paused("scanner")


def test_why_and_ambiguous(sent):
    db.upsert_coin({"mint": "A1", "chain": "solana", "name": "Dog", "symbol": "DOG"})
    db.upsert_coin({"mint": "B2", "chain": "robinhood", "name": "Dog two", "symbol": "DOG"})
    run(telegram.handle_text(7, "why DOG"))
    assert "Which one?" in sent[-1]["text"]
    assert links.coin_url("A1", "solana").replace("&", "&amp;") in sent[-1]["text"]
    db.insert("audits", {"mint": "A1", "ts": db.now(), "layer": 4, "name": "Dev wallet", "value_text": "31%",
                         "pass": 0, "source": "rpc"})
    run(telegram.handle_text(7, "why A1"))
    assert "Auditor stopped it: layer 4 Dev wallet, value 31%" in sent[-1]["text"]


def test_live_needs_paper_night(sent):
    run(telegram.handle_text(7, "live"))
    assert "1 full PAPER night" in sent[-1]["text"]
    report.ensure_night()
    report.end_night()
    run(telegram.handle_text(7, "live"))
    run(telegram.handle_text(7, "LIVE"))
    assert config.get("desk.mode") == "LIVE"


def test_push_paper_prefix_and_unread(sent):
    db.kv_set("owner_chat_id", 7)
    run(notify.push("Bought X", [("Buy on FOMO", "https://example.com"), ("Bought", "cb:bought:1")], kind="signal"))
    assert sent[-1]["text"].startswith("PAPER · Bought X")
    assert db.kv_get("unread") == 1
    kb = notify.keyboard([("Buy on FOMO", "https://e"), ("Bought", "cb:bought:1")])
    assert kb == {"inline_keyboard": [[{"text": "Buy on FOMO", "url": "https://e"}],
                                      [{"text": "Bought", "callback_data": "cb:bought:1"}]]}
    assert notify.with_url_lines("t", [("A", "https://e"), ("B", "cb:x")]) == "t\nA: https://e"


def test_morning_report_ends_night(sent, tmp_path):
    db.kv_set("owner_chat_id", 7)
    n, _ = report.ensure_night()
    db.upsert_coin({"mint": "R1", "chain": "solana", "symbol": "RED"})
    db.insert("positions", {"mint": "R1", "opened_at": db.now(), "entry_price": 1, "exit_price": 0.94,
                            "size_usd": 50, "pnl_usd": -3.0, "fees_usd": 1.9, "status": "closed",
                            "closed_at": db.now(), "mode": "PAPER", "exit_reason": "stop"})
    db.insert("skips", {"mint": "Z", "until": db.now() + 99})
    url = run(report.run_morning())
    files = list(tmp_path.glob("*.html"))
    assert len(files) == 1
    html = files[0].read_text()
    assert "RED" in html and "New to FOMO? " in html and links.signup_url() in html
    assert links.coin_url("R1", "solana").replace("&", "&amp;") in html
    assert db.current_night()["id"] != n["id"] and db.q("SELECT * FROM skips") == []
    assert "/reports/" in url and sent[-1]["buttons"][0][0] == "Open report"
