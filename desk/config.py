"""Settings with defaults, ranges, aliases. Stored in db.settings, every change in db.change_log.

Keys are "<group>.<name>". Read with get("risk.stop_pct"). Never read env for tunables.
Secrets (keys, tokens) come only from the environment, never from here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Spec:
    default: Any
    lo: float | None = None
    hi: float | None = None
    choices: tuple | None = None
    label: str = ""


S: dict[str, Spec] = {
    # scanner (Solana: pumpfun graduates)
    "scanner.solana": Spec(True, choices=(True, False), label="Solana pumpfun"),
    "scanner.min_liquidity_usd": Spec(40000, 1000, 5_000_000, label="Min liquidity $"),
    "scanner.max_top10_pct": Spec(25, 1, 100, label="Max top 10 %"),
    "scanner.max_h1_change_pct": Spec(80, 5, 10000, label="Max 1h change %"),
    "scanner.poll_s": Spec(60, 15, 600, label="Poll s"),
    "scanner.min_pool_age_min": Spec(10, 0, 240, label="Min pool age min"),
    "scanner.max_wait_min": Spec(60, 5, 720, label="Max wait min"),
    "scanner.runner_h24_pct": Spec(300, 50, 100000, label="Runner 24h %"),
    # scanner (Robinhood Chain: Pons launches scored by GROKHOOD)
    "scanner.robinhood": Spec(True, choices=(True, False), label="Robinhood Chain (GROKHOOD)"),
    "scanner.rh_min_liquidity_usd": Spec(5000, 0, 5_000_000, label="RH min liquidity $"),
    # auditor
    "auditor.enabled": Spec(True, choices=(True, False), label="Take apart before buying"),
    "auditor.max_dev_pct": Spec(5, 0, 100, label="Max dev %"),
    "auditor.max_top10_pct": Spec(25, 1, 100, label="Max top 10 %"),
    "auditor.min_lp_locked_pct": Spec(99, 0, 100, label="Min LP locked %"),
    "auditor.min_pool_age_min": Spec(10, 0, 240, label="Min pool age min"),
    "auditor.max_bundled": Spec(5, 1, 100, label="Max bundled wallets"),
    "auditor.max_delegated_accounts": Spec(10, 1, 1000, label="Max delegated accounts"),
    "auditor.retries": Spec(3, 0, 10, label="Retries"),
    "auditor.rh_min_score": Spec(70, 0, 100, label="Min GROKHOOD Safety Score"),
    # analyst
    "analyst.mode": Spec("rules", choices=("grok", "rules"), label="Judge"),
    "analyst.min_alive_6h": Spec(0.70, 0, 1, label="Min alive 6h"),
    "analyst.min_organic": Spec(0.65, 0, 1, label="Min organic"),
    "analyst.max_chase": Spec(0.30, 0, 1, label="Max chase"),
    "analyst.min_score": Spec(70, 0, 100, label="Min score"),
    "analyst.timeout_s": Spec(20, 3, 120, label="Timeout s"),
    "analyst.grok_model": Spec("grok-4.20-0309-non-reasoning", label="Grok model"),
    "analyst.grok_daily_usd": Spec(1.0, 0, 100, label="Grok daily budget $"),
    "analyst.alive_volume_h1": Spec(25000, 100, 10_000_000, label="Rules: alive volume 1h $"),
    "analyst.organic_buys_h1": Spec(150, 1, 100000, label="Rules: organic buys 1h"),
    "analyst.bundled_penalty": Spec(0.5, 0, 1, label="Rules: bundled penalty"),
    "analyst.bundled_from": Spec(3, 1, 100, label="Rules: bundled from wallets"),
    "analyst.flush_drop_m5_pct": Spec(5, 0, 100, label="Rules: flush drop 5m %"),
    "analyst.chase_h1_pct": Spec(80, 1, 10000, label="Rules: chase 1h %"),
    # executor
    "executor.stack_pct": Spec(8, 0.5, 100, label="Stack %"),
    "executor.max_pool_pct": Spec(2, 0.1, 50, label="Max % of pool"),
    "executor.min_ticket_usd": Spec(47.50, 1, 100000, label="Min ticket $"),
    "executor.below_min": Spec("bump", choices=("bump", "skip"), label="Below min"),
    "executor.recheck_liq_drop_pct": Spec(20, 1, 100, label="Recheck liq drop %"),
    "executor.recheck_price_up_pct": Spec(15, 1, 1000, label="Recheck price up %"),
    "executor.confirm_window_min": Spec(5, 1, 60, label="Confirm window min"),
    "executor.max_open": Spec(5, 1, 50, label="Max open positions"),
    # risk
    "risk.poll_s": Spec(15, 5, 300, label="Poll s"),
    "risk.stop_pct": Spec(6, 0.5, 90, label="Stop %"),
    "risk.trail_start_pct": Spec(20, 1, 1000, label="Trail starts at +%"),
    "risk.trail_pct": Spec(12, 1, 90, label="Trail %"),
    "risk.timeout_h": Spec(3, 0.25, 168, label="Timeout h"),
    "risk.timeout_min_gain_pct": Spec(5, 0, 1000, label="Timeout min gain %"),
    "risk.confirm_window_min": Spec(2, 1, 60, label="Confirm window min"),
    # desk
    "desk.mode": Spec("PAPER", choices=("PAPER", "LIVE"), label="Mode"),
    "desk.paper_start_stack": Spec(200, 10, 10_000_000, label="Paper start stack $"),
    "desk.live_stack": Spec(0, 0, 100_000_000, label="Live stack $"),
    "desk.report_time": Spec("08:00", label="Report time"),
    "desk.rpc_url": Spec("https://api.mainnet-beta.solana.com", label="Solana RPC"),
    "desk.push_rejects": Spec(False, choices=(True, False), label="Push rejects"),
    "desk.fees_ref_discount": Spec(0.10, 0, 1, label="Ref fee discount"),
    "desk.fomo_handle": Spec("", label="FOMO handle"),
    "desk.grokhood_api": Spec("https://api.grokhood.tech", label="GROKHOOD API"),
}

ALIASES = {
    "stop": "risk.stop_pct", "trail": "risk.trail_pct", "trail start": "risk.trail_start_pct",
    "timeout": "risk.timeout_h", "stack pct": "executor.stack_pct", "stack": "desk.live_stack",
    "min liquidity": "scanner.min_liquidity_usd", "min score": "analyst.min_score",
    "judge": "analyst.mode", "mode": "desk.mode", "handle": "desk.fomo_handle",
}

_cache: dict[str, Any] = {}


class SettingError(ValueError):
    pass


def resolve(name: str) -> str:
    n = name.strip().lower()
    if n in S:
        return n
    if n in ALIASES:
        return ALIASES[n]
    hits = [k for k in S if k.split(".", 1)[1] == n.replace(" ", "_")]
    if len(hits) == 1:
        return hits[0]
    raise SettingError(f"unknown setting {name!r}")


def coerce(key: str, value: Any) -> Any:
    spec = S[key]
    d = spec.default
    try:
        if isinstance(d, bool):
            v = value.strip().lower() in ("1", "true", "on", "yes") if isinstance(value, str) else bool(value)
        elif isinstance(d, (int, float)):
            v = float(str(value).replace("%", "").replace("$", "").replace(",", ""))
            if isinstance(d, int) and v == int(v):
                v = int(v)
        else:
            v = str(value).strip()
    except (TypeError, ValueError):
        raise SettingError(f"{key}: not a valid value: {value!r}")
    if isinstance(v, str) and spec.choices:
        for c in spec.choices:
            if str(c).upper() == v.upper():
                v = c
    if spec.choices is not None and v not in spec.choices:
        raise SettingError(f"{key}: one of {', '.join(map(str, spec.choices))}")
    if spec.lo is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
        if not (spec.lo <= v <= spec.hi):
            raise SettingError(f"{key}: range {spec.lo:g} to {spec.hi:g}")
    return v


def get(key: str) -> Any:
    return _cache.get(key, S[key].default)


def load(rows: dict[str, str]) -> None:
    """Load stored values (from db) over defaults. Bad stored values fall back to default."""
    _cache.clear()
    for k, raw in rows.items():
        if k in S:
            try:
                _cache[k] = coerce(k, json.loads(raw))
            except Exception:
                pass
    if "analyst.mode" not in rows:
        _cache["analyst.mode"] = "grok" if os.environ.get("XAI_API_KEY") else "rules"


def set_value(key: str, value: Any) -> tuple[str, Any, Any]:
    """Validate and set in cache. Returns (key, old, new). Caller persists via db.save_setting."""
    key = resolve(key)
    new = coerce(key, value)
    old = get(key)
    _cache[key] = new
    return key, old, new


def all_settings() -> dict[str, dict]:
    return {k: {"value": get(k), "default": s.default, "min": s.lo, "max": s.hi,
                "choices": list(s.choices) if s.choices else None, "label": s.label}
            for k, s in S.items()}


def stack() -> float:
    """Current stack: PAPER = simulated balance (db), LIVE = desk.live_stack."""
    from . import db
    if get("desk.mode") == "LIVE":
        return float(get("desk.live_stack"))
    return db.paper_balance()


def ticket_check(stack_usd: float) -> dict:
    """Ticket check. Returns {ok, min_stack, message}."""
    pct = float(get("executor.stack_pct"))
    mt = float(get("executor.min_ticket_usd"))
    min_stack = mt / (pct / 100)
    if stack_usd >= min_stack:
        return {"ok": True, "min_stack": round(min_stack, 2), "message": None}
    if get("executor.below_min") == "bump":
        p = round(100 * mt / stack_usd, 1) if stack_usd > 0 else 100
        return {"ok": True, "min_stack": round(min_stack, 2),
                "message": f"tickets bumped to ${mt:.2f} = {p}% of stack"}
    return {"ok": False, "min_stack": round(min_stack, 2),
            "message": f"every coin will be skipped, stack under ${min_stack:,.2f}"}
