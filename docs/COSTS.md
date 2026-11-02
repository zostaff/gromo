# Costs

## Analyst judge: xAI Grok

Checked 2026-11-02 on https://docs.x.ai/docs/models (USD per 1M tokens, prompt under 200k tokens;
at 200k tokens and over every rate doubles, the analyst never gets near that).

| Model id | Input | Cached input | Output |
|---|---|---|---|
| grok-4.7 | 2.00 | 0.50 | 6.00 |
| grok-4.6 | 2.00 | 0.50 | 6.00 |
| grok-4.5 | 2.00 | 0.30 | 6.00 |
| grok-4.3 | 1.25 | 0.20 | 2.50 |
| grok-4.20-0309-reasoning | 1.25 | 0.20 | 2.50 |
| grok-4.20-0309-non-reasoning | 1.25 | 0.20 | 2.50 |
| grok-4.20-multi-agent-0309 | 1.25 | 0.20 | 2.50 |
| grok-build-0.1 | 1.00 | 0.20 | 2.00 |

Same table lives in `desk/agents/analyst.py` `PRICES`. Update both together.

The default `analyst.grok_model` in `desk/config.py` is `grok-4-fast-non-reasoning`. That id is NOT on the
current xAI models page (third-party trackers list the grok-4-fast family as deprecated). Until the default
is changed, the analyst prices it at the most expensive row above (so the budget is never undercounted),
and if xAI rejects the id every call ends as "no answer". Suggested setting: `grok-4.20-0309-non-reasoning`
(cheapest general non-reasoning model listed, structured outputs supported in the Grok 4 family).

### Per call

One call = system prompt + state text (max 3000 chars) in, four numbers out.
About 1,000 input tokens and 60 output tokens:
`1000 * 1.25 / 1e6 + 60 * 2.50 / 1e6 = $0.0014` on grok-4.20-0309-non-reasoning.
The default daily budget `analyst.grok_daily_usd = 1.0` covers roughly 700 coins a day.

### How it is counted

- Cost per call = (prompt - cached) * input + cached * cached input + (completion + reasoning) * output,
  from the `usage` block of the answer. Reasoning tokens are added on top of completion tokens, which can
  only over-count.
- Daily total (UTC day) in `db.kv` key `grok_cost:YYYY-MM-DD` = `{"calls", "costUsd"}`, also in
  `verdicts.cost_usd` per coin. `/api/account` reads it through `analyst.grok_today()`.
- Before each call: spent today + estimate of this call > `analyst.grok_daily_usd` -> reject "grok budget".
  No call is made and the judge does not switch to rules.

## FOMO fees (PAPER PnL)

See `desk/fees.py`: per side, $5-$47.50 -> 2%, $47.50-$190 -> flat $0.95, above -> 0.50%,
times (1 - `desk.fees_ref_discount`). Gas 0.
