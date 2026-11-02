"""Analyst: four questions per coin that passed the auditor, gate in code, verdict to the executor.

Judge "grok": xAI chat completions with a strict JSON schema, calibrated probabilities.
Judge "rules": plain formulas over the market read. Never falls back from grok to rules silently:
a grok failure is a reject with its reason ("grok budget", "no answer", "bad answer", "no key").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from desk import bus, config, db, market

log = logging.getLogger("analyst")

XAI_URL = "https://api.x.ai/v1/chat/completions"

# USD per 1M tokens, prompt under 200k tokens. Source: https://docs.x.ai/docs/models (checked 2026-11-02).
# See docs/COSTS.md. A model not listed here is priced at the most expensive row (budget stays safe).
PRICES: dict[str, dict[str, float]] = {
    "grok-4.7": {"input": 2.00, "cached": 0.50, "output": 6.00},
    "grok-4.6": {"input": 2.00, "cached": 0.50, "output": 6.00},
    "grok-4.5": {"input": 2.00, "cached": 0.30, "output": 6.00},
    "grok-4.3": {"input": 1.25, "cached": 0.20, "output": 2.50},
    "grok-4.20-0309-reasoning": {"input": 1.25, "cached": 0.20, "output": 2.50},
    "grok-4.20-0309-non-reasoning": {"input": 1.25, "cached": 0.20, "output": 2.50},
    "grok-4.20-multi-agent-0309": {"input": 1.25, "cached": 0.20, "output": 2.50},
    "grok-build-0.1": {"input": 1.00, "cached": 0.20, "output": 2.00},
}
_WORST = {"input": max(p["input"] for p in PRICES.values()),
          "cached": max(p["cached"] for p in PRICES.values()),
          "output": max(p["output"] for p in PRICES.values())}

QUESTIONS = {
    "alive_6h": "still actively traded 6 hours from now",
    "organic": "most buyers in the last 30 minutes are separate real wallets",
    "flush": "price just flushed down and buyers are absorbing it",
    "chase": "this entry is chasing a green candle that already ran",
}
ANSWER_KEYS = tuple(QUESTIONS)

SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "number", "minimum": 0, "maximum": 1, "description": v}
                   for k, v in QUESTIONS.items()},
    "required": list(ANSWER_KEYS),
    "additionalProperties": False,
}

SYSTEM = (
    "You read the state of one memecoin and give calibrated probabilities, each a number from 0 to 1, "
    "for four statements. Calibrated means: of all coins you give 0.7, about 70% turn out true. "
    "Use only the numbers given; n/a means unknown, not zero. Answer only the JSON.\n"
    + "\n".join(f"{k}: probability that {v}." for k, v in QUESTIONS.items())
)

STATE_MAX = 3000

_asked: set[str] = set()


class Reject(Exception):
    pass


# ---- market read -------------------------------------------------------------------
async def market_read(chain: str, mint: str) -> dict[str, Any]:
    """{priceUsd, liquidityUsd, marketCap, vol_m5, vol_h1, buys_m5, sells_m5, buys_h1, sells_h1,
    chg_m5, chg_h1}, None for missing. The only place the analyst touches the market."""
    try:
        return await market.market_read(chain, mint)
    except Exception as e:
        log.warning("market read %s failed: %s", mint, e)
        return market.empty()


# ---- state text ----------------------------------------------------------------------
def _f(v: Any, fmt: str = "{:,.2f}") -> str:
    if v is None:
        return "n/a"
    try:
        return fmt.format(v)
    except (TypeError, ValueError):
        return str(v)


def _price(v: Any) -> str:
    return "n/a" if v is None else f"{float(v):.10g}"


def state_text(scan: dict, audit: dict, m: dict) -> str:
    """Plain numbers only: no thresholds, no balance, no word for purchasing intent."""
    chain = scan.get("chain") or audit.get("chain") or "solana"
    lines = [
        f"Coin: {scan.get('name') or 'n/a'} ({scan.get('symbol') or 'n/a'}) on {chain}",
        f"Pool age minutes: {_f(scan.get('ageMin'), '{:,.0f}')}",
        f"Price USD: {_price(m.get('priceUsd'))}",
        f"Liquidity USD now: {_f(m.get('liquidityUsd'), '{:,.0f}')}",
        f"Liquidity USD at scan: {_f(scan.get('liquidityUsd'), '{:,.0f}')}",
        f"Market cap USD: {_f(m.get('marketCap'), '{:,.0f}')}",
        f"Volume USD last 5m: {_f(m.get('vol_m5'), '{:,.0f}')}, last 1h: {_f(m.get('vol_h1'), '{:,.0f}')}",
        f"Purchases last 5m: {_f(m.get('buys_m5'), '{:,.0f}')}, sales last 5m: {_f(m.get('sells_m5'), '{:,.0f}')}",
        f"Purchases last 1h: {_f(m.get('buys_h1'), '{:,.0f}')}, sales last 1h: {_f(m.get('sells_h1'), '{:,.0f}')}",
        f"Price change last 5m %: {_f(m.get('chg_m5'))}, last 1h %: {_f(m.get('chg_h1'))}",
        f"Top 10 holders %: {_f(scan.get('top10Pct'))}",
        f"Holders: {_f(audit.get('holders'), '{:,.0f}')}",
        f"Bundled wallets: {_f(audit.get('bundled'), '{:,.0f}')}",
    ]
    if chain == "robinhood":
        lines.append(f"GROKHOOD Safety Score (0-100): {_f(audit.get('safetyScore'), '{:,.0f}')}")
        reasons = [f"{l.get('name')}: {l.get('value')}" for l in audit.get("layers") or []
                   if l.get("value") not in (None, "")][:5]
        if reasons:
            lines.append("GROKHOOD top reasons: " + "; ".join(reasons))
    else:
        facts = [f"{l.get('name')}: {l.get('value')}" for l in audit.get("layers") or []
                 if l.get("value") not in (None, "")]
        if facts:
            lines.append("Audit facts: " + "; ".join(facts))
    text = "\n".join(lines)
    text = re.sub(r"(?i)buy", "purchase", text)
    return text[:STATE_MAX]


# ---- grok ------------------------------------------------------------------------------
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def grok_today() -> dict:
    """{"calls", "costUsd"} spent today (UTC), for /api/account."""
    d = db.kv_get(f"grok_cost:{_today()}", None) or {}
    return {"calls": int(d.get("calls", 0)), "costUsd": round(float(d.get("costUsd", 0.0)), 6)}


def _add_cost(cost: float) -> None:
    key = f"grok_cost:{_today()}"
    d = db.kv_get(key, None) or {"calls": 0, "costUsd": 0.0}
    db.kv_set(key, {"calls": int(d["calls"]) + 1, "costUsd": float(d["costUsd"]) + cost})


def price_of(model: str) -> dict[str, float]:
    return PRICES.get(model, _WORST)


def call_cost(model: str, usage: dict) -> float:
    p = price_of(model)
    inp = int(usage.get("prompt_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    out = int(usage.get("completion_tokens") or 0)
    reasoning = int(((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
    # Reasoning tokens are billed as output. Counting them on top errs on the safe (higher) side.
    return ((inp - cached) * p["input"] + cached * p["cached"] + (out + reasoning) * p["output"]) / 1_000_000


def estimate_cost(model: str, prompt: str) -> float:
    p = price_of(model)
    return ((len(prompt) / 3 + 300) * p["input"] + 200 * p["output"]) / 1_000_000


def parse_answers(content: Any) -> dict[str, float]:
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        raise Reject("bad answer")
    if not isinstance(data, dict):
        raise Reject("bad answer")
    out = {}
    for k in ANSWER_KEYS:
        v = data.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0):
            raise Reject("bad answer")
        out[k] = float(v)
    return out


async def _post(client: httpx.AsyncClient, body: dict, key: str) -> dict:
    r = await client.post(XAI_URL, json=body, headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    return r.json()


async def judge_grok(state: str, client: httpx.AsyncClient | None = None) -> tuple[dict, int, str, float]:
    """-> (answers, input_tokens, model, cost_usd). Raises Reject."""
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise Reject("no key")
    model = str(config.get("analyst.grok_model"))
    budget = float(config.get("analyst.grok_daily_usd"))
    if grok_today()["costUsd"] + estimate_cost(model, SYSTEM + state) > budget:
        raise Reject("grok budget")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": state}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "coin_answers", "schema": SCHEMA, "strict": True}},
        "temperature": 0,
    }
    timeout = float(config.get("analyst.timeout_s"))
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    data = None
    try:
        for attempt in (1, 2):
            try:
                data = await asyncio.wait_for(_post(client, body, key), timeout=timeout)
                break
            except Exception as e:  # timeout, network, http status
                log.warning("grok attempt %d failed: %s", attempt, type(e).__name__)
                if attempt == 1:
                    await asyncio.sleep(2)
    finally:
        if own:
            await client.aclose()
    if data is None:
        raise Reject("no answer")
    usage = data.get("usage") or {}
    cost = call_cost(model, usage)
    _add_cost(cost)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise Reject("bad answer")
    answers = parse_answers(content)
    return answers, int(usage.get("prompt_tokens") or 0), str(data.get("model") or model), cost


# ---- rules -------------------------------------------------------------------------------
def clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def judge_rules(m: dict, audit: dict) -> dict[str, float]:
    need = ("vol_h1", "buys_h1", "chg_m5", "buys_m5", "sells_m5", "chg_h1")
    if any(m.get(k) is None for k in need):
        raise Reject("no data")
    g = config.get
    alive = clamp(m["vol_h1"] / float(g("analyst.alive_volume_h1")))
    organic = clamp(m["buys_h1"] / float(g("analyst.organic_buys_h1")))
    bundled = audit.get("bundled")
    if bundled is not None and bundled >= float(g("analyst.bundled_from")):
        organic *= float(g("analyst.bundled_penalty"))
    if m["chg_m5"] <= -float(g("analyst.flush_drop_m5_pct")) and m["buys_m5"] >= m["sells_m5"]:
        flush = 1.0
    elif m["chg_m5"] < 0:
        flush = 0.5
    else:
        flush = 0.0
    chase = clamp(m["chg_h1"] / float(g("analyst.chase_h1_pct")))
    return {"alive_6h": round(alive, 4), "organic": round(organic, 4), "flush": flush, "chase": round(chase, 4)}


# ---- gate --------------------------------------------------------------------------------
def gate(a: dict[str, float]) -> tuple[int, bool, list[str]]:
    g = config.get
    score = round(100 * (a["alive_6h"] + a["organic"] + a["flush"] + (1 - a["chase"])) / 4)
    failed = []
    if a["alive_6h"] < float(g("analyst.min_alive_6h")):
        failed.append(f"alive {a['alive_6h']:.2f}")
    if a["organic"] < float(g("analyst.min_organic")):
        failed.append(f"organic {a['organic']:.2f}")
    if a["chase"] > float(g("analyst.max_chase")):
        failed.append(f"chase {a['chase']:.2f}")
    if score < float(g("analyst.min_score")):
        failed.append(f"score {score}")
    return score, not failed, failed


# ---- one coin ----------------------------------------------------------------------------
def already_asked(mint: str) -> bool:
    return mint in _asked or db.q1("SELECT 1 AS x FROM verdicts WHERE mint=?", (mint,)) is not None


def _card(scoring: dict | None, mode: str) -> None:
    n = db.q1("SELECT COUNT(*) AS n FROM verdicts")["n"]
    db.kv_set("analyst_card", {"scoring": scoring, "scored": int(n), "mode": mode})


async def analyze(msg: dict, client: httpx.AsyncClient | None = None) -> dict | None:
    """Judge one {"scan","audit"} message. Returns the verdict JSON (None if already asked)."""
    scan, audit = msg.get("scan") or {}, msg.get("audit") or {}
    mint = scan.get("mint") or audit.get("mint")
    if not mint:
        return None
    if already_asked(mint):
        log.info("skip %s: already asked", mint)
        return None
    _asked.add(mint)
    chain = scan.get("chain") or audit.get("chain") or "solana"
    name = scan.get("name") or scan.get("symbol") or mint[:6]
    mode = str(config.get("analyst.mode"))
    _card({"mint": mint, "name": name, "score": None, "passed": None}, mode)

    m = await market_read(chain, mint)
    answers: dict | None = None
    tokens, model, cost, reason = 0, ("rules" if mode == "rules" else str(config.get("analyst.grok_model"))), 0.0, None
    try:
        if mode == "rules":
            answers = judge_rules(m, audit)
        else:
            if all(v is None for v in m.values()):
                raise Reject("no data")
            answers, tokens, model, cost = await judge_grok(state_text(scan, audit, m), client)
    except Reject as r:
        reason = str(r)

    if answers is not None:
        score, passed, failed = gate(answers)
        reason = None if passed else ", ".join(failed)
    else:
        score, passed, failed = 0, False, [reason or "no answer"]

    verdict = {"agent": "analyst", "mint": mint,
               "answers": answers or {k: None for k in ANSWER_KEYS},
               "score": score, "passed": passed, "failed": failed, "model": model,
               "inputTokens": tokens, "reason": reason}
    a = answers or {}
    db.insert("verdicts", {"mint": mint, "ts": db.now(), "alive_6h": a.get("alive_6h"),
                           "organic": a.get("organic"), "flush": a.get("flush"), "chase": a.get("chase"),
                           "score": score, "pass": int(passed), "reason": reason, "input_tokens": tokens,
                           "model": model, "cost_usd": cost})
    bus.emit("verdict", {**verdict, "name": name, "chain": chain})
    _card({"mint": mint, "name": name, "score": score, "passed": passed}, mode)

    if passed:
        await bus.executor.put({"scan": scan, "audit": audit, "verdict": verdict, "market": m,
                                "analyst_ts": time.time()})
        log.info("pass %s score %s", name, score)
    else:
        who = "Analyst (rules)" if mode == "rules" else "Analyst"
        db.add_activity("rejected", mint, "analyst", f"Rejected {name}", f"{who} · {', '.join(failed)}",
                        "Skipped", "", str(config.get("desk.mode")))
        log.info("reject %s: %s", name, ", ".join(failed))
        if config.get("desk.push_rejects"):
            from desk import links
            await market.push(f"Rejected {name}\n{who} · {', '.join(failed)}",
                              [("Open on FOMO", links.coin_url(mint, chain))], mint, "reject")
    return verdict


async def run() -> None:
    log.info("analyst up")
    async with httpx.AsyncClient(timeout=float(config.get("analyst.timeout_s"))) as client:
        while True:
            db.beat("analyst")
            if db.is_paused("analyst"):
                await asyncio.sleep(2)
                continue
            try:
                msg = await asyncio.wait_for(bus.analyst.get(), timeout=10)
            except asyncio.TimeoutError:
                continue
            try:
                await analyze(msg, client)
            except Exception:
                log.exception("analyst failed on a message")
