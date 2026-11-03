"""Auto trading: limits, real buy -> position, real sell -> close, failures. Jupiter is faked, no network."""
import asyncio

import pytest

from desk import db, swap, wallet
from desk.agents import executor, risk
from desk.tests.conftest import setv


class FakeSwap:
    def __init__(self):
        self.buys, self.sells = [], []
        self.fail_buy = self.fail_sell = None
        self.usd_out = None

    async def buy(self, mint, usd):
        if self.fail_buy:
            raise swap.SwapError(self.fail_buy)
        self.buys.append((mint, usd))
        # 1,000,000 raw tokens with 6 decimals = 1 token for `usd` dollars
        return {"signature": "SIGB", "usd_in": usd, "tokens_raw": 1_000_000, "decimals": 6, "price_usd": usd}

    async def sell(self, mint, raw):
        if self.fail_sell:
            raise swap.SwapError(self.fail_sell)
        self.sells.append((mint, raw))
        return {"signature": "SIGS", "usd_out": self.usd_out, "tokens_raw": raw, "price_usd": self.usd_out}

    async def wallet_usd(self):
        return 200.0

    async def sol_usd(self):
        return 100.0


@pytest.fixture
def live(monkeypatch, fake_market, pushes):
    fs = FakeSwap()
    for n in ("buy", "sell", "wallet_usd", "sol_usd"):
        monkeypatch.setattr(swap, n, getattr(fs, n))
    monkeypatch.setattr(wallet, "available", lambda: True)
    setv("desk.mode", "LIVE")
    setv("trade.auto", True)
    db.kv_set("wallet_usd", 200.0)
    return fs


def _msg(mint="A", liq=40_000, price=0.01, chain="solana"):
    return {"scan": {"chain": chain, "mint": mint, "name": f"C{mint}", "symbol": mint, "liquidityUsd": liq},
            "audit": {"layers": [{"n": i, "pass": True} for i in range(1, 10)]},
            "verdict": {"mint": mint, "score": 80}, "market": {"priceUsd": price}}


def test_buy_capped_at_max_ticket_and_opens_position(live, fake_market, pushes):
    fake_market.set("A", priceUsd=0.01, liquidityUsd=40_000)
    setv("executor.stack_pct", 50)    # 50% of $200 = $100, capped at $25
    r = asyncio.run(executor.handle(_msg()))
    assert r["status"] == "bought" and live.buys == [("A", 25.0)]
    p = db.q1("SELECT * FROM positions")
    assert p["mode"] == "LIVE" and p["qty_raw"] == "1000000" and p["buy_sig"] == "SIGB" and p["size_usd"] == 25
    assert pushes[-1]["text"].startswith("Bought CA · $25.00")


def test_free_sol_minus_reserve_limits_size(live, fake_market, monkeypatch):
    async def poor():
        return 8.0   # $8 in SOL, reserve 0.03 SOL = $3 -> $5 free = min ticket
    monkeypatch.setattr(swap, "wallet_usd", poor)
    setv("executor.stack_pct", 100)
    fake_market.set("A", priceUsd=0.01, liquidityUsd=40_000)
    r = asyncio.run(executor.handle(_msg()))
    assert r["status"] == "bought" and live.buys == [("A", 5.0)]

    async def empty():
        return 4.0
    monkeypatch.setattr(swap, "wallet_usd", empty)
    fake_market.set("B", priceUsd=0.01, liquidityUsd=40_000)
    assert asyncio.run(executor.handle(_msg("B")))["reason"] == "wallet empty"


def test_max_open_trades(live, fake_market):
    for m in "ABC":
        fake_market.set(m, priceUsd=0.01, liquidityUsd=40_000)
        assert asyncio.run(executor.handle(_msg(m)))["status"] == "bought"
    fake_market.set("D", priceUsd=0.01, liquidityUsd=40_000)
    assert asyncio.run(executor.handle(_msg("D")))["reason"] == "max open trades"
    assert len(live.buys) == 3


def test_night_loss_limit_stops_buying(live, fake_market, pushes):
    db.start_night(200, "LIVE")
    fake_market.set("A", priceUsd=0.01, liquidityUsd=40_000)
    asyncio.run(executor.handle(_msg("A")))
    pid = db.q1("SELECT id FROM positions")["id"]
    live.usd_out = 0.0   # sold for nothing: -$16 (8% of $200)
    asyncio.run(risk.auto_sell(db.q1("SELECT * FROM positions WHERE id=?", (pid,)), "stop"))
    setv("trade.daily_loss_usd", 15)
    fake_market.set("B", priceUsd=0.01, liquidityUsd=40_000)
    assert asyncio.run(executor.handle(_msg("B")))["reason"] == "night loss limit hit"
    assert any("Night loss limit" in p["text"] for p in pushes)


def test_buy_failure_is_a_skip_with_reason(live, fake_market, pushes):
    live.fail_buy = "no route: Insufficient funds"
    fake_market.set("A", priceUsd=0.01, liquidityUsd=40_000)
    r = asyncio.run(executor.handle(_msg()))
    assert r["status"] == "failed" and db.q1("SELECT COUNT(*) n FROM positions")["n"] == 0
    assert "buy failed" in db.q1("SELECT subtitle FROM activity WHERE kind='skipped'")["subtitle"]
    assert pushes[-1]["text"].startswith("Buy failed CA")


def test_robinhood_stays_on_buttons(live, fake_market, pushes):
    fake_market.set("0xabc", priceUsd=0.01, liquidityUsd=40_000)
    setv("desk.live_stack", 1000)
    r = asyncio.run(executor.handle(_msg("0xabc", chain="robinhood"), paper_bar=False))
    assert r["status"] == "sent" and live.buys == []


def test_stop_sells_for_real_and_books_real_pnl(live, fake_market, pushes):
    fake_market.set("A", priceUsd=0.01, liquidityUsd=40_000)
    asyncio.run(executor.handle(_msg()))
    p = db.q1("SELECT * FROM positions")
    entry = p["entry_price"]
    live.usd_out = 14.10
    fake_market.set("A", priceUsd=entry * 0.93, liquidityUsd=40_000)
    asyncio.run(risk.poll_once())
    p = db.q1("SELECT * FROM positions")
    assert p["status"] == "closed" and p["exit_reason"] == "stop" and p["pnl_usd"] == -1.9 and p["sell_sig"] == "SIGS"
    assert live.sells == [("A", 1_000_000)]


def test_failed_sell_retries_each_poll_and_pushes_once(live, fake_market, pushes):
    fake_market.set("A", priceUsd=0.01, liquidityUsd=40_000)
    asyncio.run(executor.handle(_msg()))
    entry = db.q1("SELECT entry_price FROM positions")["entry_price"]
    live.fail_sell = "swap failed: slippage"
    fake_market.set("A", priceUsd=entry * 0.9, liquidityUsd=40_000)
    for _ in range(4):
        asyncio.run(risk.poll_once())
    p = db.q1("SELECT * FROM positions")
    assert p["status"] == "exiting" and p["sell_tries"] == 4
    assert sum("SELL FAILED" in x["text"] for x in pushes) == 1
    live.fail_sell, live.usd_out = None, 22.0
    asyncio.run(risk.poll_once())
    assert db.q1("SELECT status FROM positions")["status"] == "closed"


def test_auto_off_falls_back_to_buttons(live, fake_market):
    setv("trade.auto", False)
    setv("desk.live_stack", 1000)
    fake_market.set("A", priceUsd=0.01, liquidityUsd=40_000)
    r = asyncio.run(executor.handle(_msg(), paper_bar=False))
    assert r["status"] == "sent" and live.buys == []
