"""Shared fixtures: in-memory db, default settings, fake market read, captured pushes. No network."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desk import config, db, market  # noqa: E402


@pytest.fixture(autouse=True)
def mem_db(monkeypatch):
    db.use(":memory:")
    config._cache.clear()
    config._cache["analyst.mode"] = "rules"
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    from desk import bus
    for qu in (bus.auditor, bus.analyst, bus.executor, bus.risk):
        while not qu.empty():
            qu.get_nowait()
    from desk.agents import analyst, risk
    analyst._asked.clear()
    risk._repush.clear()
    yield db
    config._cache.clear()


class FakeMarket:
    """market_reads replacement: set .data[mint] = {priceUsd: ..., ...}."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.calls: list[tuple[str, list[str]]] = []

    def set(self, mint: str, **kw) -> None:
        d = self.data.setdefault(mint, market.empty())
        d.update(kw)

    async def reads(self, chain: str, mints: list[str]) -> dict[str, dict]:
        self.calls.append((chain, list(mints)))
        return {m: dict(self.data.get(m) or market.empty()) for m in mints}


@pytest.fixture
def fake_market(monkeypatch):
    fm = FakeMarket()
    monkeypatch.setattr(market, "market_reads", fm.reads)
    return fm


@pytest.fixture
def pushes(monkeypatch):
    sent: list[dict] = []

    async def push(text, buttons=None, mint=None, kind="info"):
        sent.append({"text": text, "buttons": list(buttons or []), "mint": mint, "kind": kind})

    monkeypatch.setitem(sys.modules, "desk.notify", types.SimpleNamespace(push=push))
    return sent


def setv(key, value):
    config._cache[key] = config.coerce(key, value)
