"""Copy bot cards: unknown chain, breaking dedupe, buy/sell inference, filters, GROKHOOD line.
Fake swaps use the candidate paths of copybot/fields.py (FOMO keys are not verified yet)."""
import json
from pathlib import Path

import pytest

from copybot import fields, links, store, tracker
from copybot.tracker import Breaking, breaking_card, build_trade, effective_interval, infer_side, passes, trade_card

ROOT = Path(__file__).resolve().parents[2]
TRADER = {"id": "u1", "handle": "alice"}
COIN = "0x6511c233790ec10f7d673c64dc82c3f941d57828"


def swap(sid, in_sym, in_addr, out_sym, out_addr, chain_id=4663, usd=120.0):
    return {"id": sid, "createdAt": "2026-11-02T01:00:00Z", "chainId": chain_id, "usdValue": usd,
            "tokenIn": {"symbol": in_sym, "address": in_addr}, "tokenOut": {"symbol": out_sym, "address": out_addr}}


@pytest.mark.parametrize("quote", ["USDC", "USDT", "SOL", "WETH"])
def test_swap_into_quote_is_sell(quote):
    side, sym, addr = infer_side("OAK", COIN, quote, "x")
    assert (side, sym, addr) == ("sell", "OAK", COIN)
    side, sym, addr = infer_side(quote, "x", "OAK", COIN)
    assert (side, sym, addr) == ("buy", "OAK", COIN)


def test_sell_by_quote_address_without_symbol():
    assert infer_side(None, COIN, None, "So11111111111111111111111111111111111111112")[0] == "sell"


def test_build_trade_and_card_robinhood():
    tr = build_trade(swap("s1", "ETH", "0x0000000000000000000000000000000000000000", "OAK", COIN), TRADER)
    assert tr["side"] == "buy" and tr["chain"] == "robinhood" and tr["usd"] == 120.0
    assert tr["mcap"] is None and tr["liq"] is None  # unknown stays None, never invented
    text, btns = trade_card(tr, "en", {"score": {"value": 82, "level": "safe"}, "ring": None})
    assert links.profile_url("alice") in text.replace("&amp;", "&")
    assert links.coin_url(COIN, "robinhood") in text.replace("&amp;", "&")
    assert "mcap n/a" in text and "GROKHOOD 82/100" in text
    assert btns == [("Copy OAK", links.coin_url(COIN, "robinhood"))]


def test_unknown_chain_card_opens_fomo_signup():
    tr = build_trade(swap("s2", "USDC", "u", "ZZZ", "zaddr", chain_id=999999), TRADER)
    assert tr["chain"] is None
    text, btns = trade_card(tr, "en")
    assert btns == [("Open FOMO", links.signup_url())]
    assert "unknown chain" in text
    assert links.signup_url() in text


def test_grokhood_warning_lines_on_real_samples():
    raw = ROOT / "logs" / "raw" / "grokhood_token_biaoqing.json"
    gh = json.loads(raw.read_text()) if raw.exists() else {"score": {"value": 65}, "verdict": {"level": "danger",
                                                                                              "label": "Dev sold"}}
    assert tracker.gh_line(gh) == "GROKHOOD warning: Dev sold"
    assert tracker.gh_line({"ring": {"id": 1}, "score": {"value": 90}}) == "GROKHOOD: rug ring"
    assert tracker.gh_line({}) is None


def test_breaking_needs_n_distinct_traders_and_dedupes_per_window():
    b = Breaking()
    mk = lambda tid, ts: {**build_trade(swap(f"s{tid}{ts}", "ETH", "0x0000000000000000000000000000000000000000",
                                              "OAK", COIN), {"id": tid, "handle": tid}), "ts": ts}
    W = 30 * 60
    assert b.add(mk("a", 0), 3, W, 0) is None
    assert b.add(mk("a", 10), 3, W, 10) is None      # same trader twice does not count
    assert b.add(mk("b", 20), 3, W, 20) is None
    al = b.add(mk("c", 30), 3, W, 30)
    assert al and al["n"] == 3 and set(al["handles"]) == {"a", "b", "c"}
    assert b.add(mk("d", 40), 3, W, 40) is None      # 1 per coin per window
    text, btns = breaking_card(al)
    assert text.startswith("BREAKING: 3 followed traders bought") and btns[0][0] == "Copy OAK"
    # after the window a new wave can alert again
    for i, tid in enumerate("xyz"):
        res = b.add(mk(tid, W + 100 + i), 3, W, W + 100 + i)
    assert res is not None


def test_breaking_ignores_sells_and_old_buys():
    b = Breaking()
    sell = build_trade(swap("q1", "OAK", COIN, "USDC", "u"), TRADER)
    assert b.add(sell, 1, 1800, 0) is None
    W = 1800
    for i, tid in enumerate("ab"):
        b.add({**build_trade(swap(f"o{i}", "ETH", "e", "OAK", COIN), {"id": tid, "handle": tid}), "ts": 0}, 3, W, 0)
    late = {**build_trade(swap("o9", "ETH", "e", "OAK", COIN), {"id": "c", "handle": "c"}), "ts": W + 5}
    assert b.add(late, 3, W, W + 5) is None  # a and b are out of the window


def test_filters():
    f = dict(store.DEFAULT_FILTERS)
    buy = build_trade(swap("f1", "ETH", "e", "OAK", COIN, usd=120), TRADER)
    assert passes(buy, f)
    assert not passes({**buy, "usd": 10}, f)
    assert not passes({**buy, "mcap": 10_000}, f)
    assert not passes({**buy, "liq": 5_000}, f)
    assert not passes({**buy, "side": "sell"}, {**f, "sells": False})
    assert not passes(buy, {**f, "networks": ["solana"]})


def test_interval_budget():
    assert effective_interval(10, 30) == 30          # 10 * 2 = 20/min
    assert effective_interval(100, 30) == 50         # 100 * 60/50 = 120/min
    assert effective_interval(0, 30) == 30


def test_missing_keys_are_none():
    assert fields.fomo({}, "swap.usd") is None
    assert fields.fomo_list({"data": {"x": 1}}, "swaps.list") == []
    assert build_trade({"tokenIn": {}}, TRADER) is None  # no id: cannot dedupe, skipped
