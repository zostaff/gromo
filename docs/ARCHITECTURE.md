# GROKHOOD x FOMO desk: architecture and contracts

One always-on Python 3.12+ process (asyncio): 5 agents + HTTP API + SSE + Telegram long polling.
SQLite WAL (`desk/db.py`). Plain HTML/CSS/JS iPhone web app in `desk/web/` (no framework, no build).
A separate copy bot in `copybot/` (FOMO trader tracker, Telegram).

Two chains, one pipeline:
- **solana**: pumpfun graduates (PumpPortal `subscribeMigration` websocket), DexScreener, RugCheck, Solana RPC.
- **robinhood**: Pons launches on Robinhood Chain from the GROKHOOD API (`https://api.grokhood.tech/v1`,
  SSE `/v1/stream` events `launch`/`score`/`dump`, token card `/v1/tokens/:address`, holders, map, alerts).
  GROKHOOD is our own rug-ring scanner: Safety Score 0-100, rings, known operators, insider dump alerts.
  FOMO trades Robinhood Chain (chainId 4663), so both chains end at the same FOMO button.

FOMO has no public order API. The desk never places orders, never holds keys, never touches funds.
It sends a button; the human taps Buy / Sell inside FOMO.

## Rules for every module
- Python 3.12+ syntax, type hints, `httpx.AsyncClient`, no blocking calls in the loop.
- FOMO URLs only from `desk/links.py` (`coin_url`, `profile_url`, `signup_url`). Never build one by hand.
- Settings only via `desk.config.get("group.name")`. Secrets only from env: `XAI_API_KEY`, `TG_TOKEN`,
  `APP_TOKEN`, `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `FOMO_REF`, `FOMO_TOKEN` (copy bot). Never log or return them.
- Raw JSON keys of external sources are read only through `desk/fields.py` (mapping proven on samples in `logs/raw/`).
  A key that is not there = None. Never guess.
- No made-up numbers anywhere. Missing data shows as null / "n/a".
- Every agent calls `db.beat(name)` each loop and respects `db.is_paused(name)`.
- No em or en dashes in any user-facing string (UI, pushes, README). Use hyphen, colon, comma.
- Logs: `logs/<agent>.log` via `logging` (main.py sets up handlers).

## Bus messages (dicts on `desk/bus.py` queues)
Scanner -> `bus.auditor`:
```
{"agent":"scanner","chain":"solana|robinhood","mint":str,"name":str,"symbol":str,"pool":str|None,
 "liquidityUsd":float,"top10Pct":float|None,"h1ChangePct":float|None,"ageMin":int,
 "verdict":"pass","reason":None,"ts":iso, "image":str|None}
```
Auditor -> `bus.analyst`: `{"scan": <scanner msg>, "audit": <auditor JSON>}`
```
auditor JSON = {"agent":"auditor","mint":str,"chain":str,"ts":iso,"passed":bool,"layersChecked":int,
 "layers":[{"n":1,"name":"Mint authority","pass":bool|None,"value":str,"source":"rpc|rugcheck|grokhood"}],
 "holders":int|None,"reason":str|None, "bundled":int|None, "safetyScore":int|None}
```
pass None = "not checked" (grey). Layers emitted live: `bus.emit("audit_layer", {"mint","n","name","pass","value"})`.
9 layers per chain, in this order, stop at the first red. Layer 9 runs only when `auditor.layer9` is on
(default on); off = 8 layers and the shield reads "8/8". The shield everywhere ("9/9" in the push text, API,
auditor card) is passed layers / layers in that audit, never a hardcoded 8.
Solana: 1 Mint authority, 2 Freeze authority, 3 Dev wallet, 4 Top 10 holders, 5 LP locked, 6 Pool age,
7 Bundled wallets, 8 Delegations, 9 Dev history (RugCheck report `creatorTokens`, the creator's other
pump.fun launches, no extra call: same report as layers 3/5). Red when the creator launched
>= auditor.dev_max_launches_7d (5) coins in the last 7 days ("41 launches in 7d"), or when at least 3 of its
coins older than 1 day are judged and more than auditor.dev_max_dead_pct (50) % of them are dead (marketCap
< auditor.dev_dead_mcap_usd, $5,000). Ok: "No previous launches" or "3 launches · 0 dead" ("50+" when
RugCheck's list of 50 is full). `creatorTokens` key missing = no data.
Robinhood: 9 layers from GROKHOOD: 1 Safety Score >= auditor.rh_min_score, 2 Rings (none), 3 Known operator
(none), 4 Dev wallet (deployer share/sold), 5 Top 10 holders, 6 Exempt/snipe wallets, 7 Bundled/clusters
(top cluster from /map), 8 Dump alerts (none at warn/danger), 9 X activity (`/v1/tokens/:a/x-activity`):
red when the level is worse than auditor.max_shill_level (default "mixed": spam is red "Coordinated shill ·
9 accounts"; set "organic" to also refuse mixed). Ok "Organic · 14 posts" / "Mixed · 14 posts".
Tracked with no posts ("No X posts naming this token were found yet.") = ok "No X posts".
Not tracked (`tracked:false`) or 404 = red "no data: x activity" after the retries.

Analyst -> `bus.executor`: `{"scan":..., "audit":..., "verdict": <analyst JSON>}`
```
{"agent":"analyst","mint":str,"answers":{"alive_6h":f,"organic":f,"flush":f,"chase":f},"score":int,
 "passed":bool,"failed":["chase 0.41"],"model":"grok-...|rules","inputTokens":int,"reason":str|None}
```
Executor opens positions (db.positions) and puts `{"position_id": int}` on `bus.risk`.

## Activity rows (`db.add_activity`)
kind / title / subtitle / value / value2:
- bought  / "Bought X"  / "Executor · 3:58 AM" / "$93.46" / "8% of stack"
- sold    / "Sold X"    / "Risk · trailing stop" / "+$204.12" / "+80.8%"
- stopped / "Stopped X" / "Risk · -6% stop" / "-$7.29" / "-6.0%"
- timeout / "Sold X"    / "Risk · dead money" / "+$1.10" / "+1.2%"
- rejected/ "Rejected X"/ "Auditor · dev holds 31%" / "Skipped" / ""   (Analyst: "Analyst · chase 0.41")
Time strings in the user's local time are formatted by the app, the subtitle carries a short UTC-free label.

## SSE events (`bus.emit(type, data)`)
`scan`, `audit_layer`, `audit`, `verdict`, `signal`, `fill`, `exit`, `balance`, `activity`, `price`, `agent`.

## HTTP API (desk/api.py, FastAPI). Every /api call needs `Authorization: Bearer APP_TOKEN`
(or `?t=` for SSE since EventSource cannot set headers), else 401. Static `web/` served at `/`.
Response shapes (the app reads exactly these):

`GET /api/state`
```
{"balance":float,"todayPct":float,"multiplier":float,"mode":"PAPER|LIVE","nightStart":float,
 "online":true,"updatedAt":ts,"unread":int,"handle":str,
 "agents":[{"name":"scanner","status":"running|paused|down","count":int,"subtitle":str,
            "card":{...agent specific, see below}}]}
```
Agent `card` payloads:
- scanner: `{"pools":int,"feed":[{"name","symbol","mint","chain","verdict":"pass|kill","reason","liq"}] (last 6)}`
- auditor: `{"takenApart":int,"rejected":int,"current":{"mint","name","layers":[{"n","pass"}]},"last":"9/9 → Executor|<reason>"}`
- analyst: `{"scoring":{"mint","name","score":int,"passed":bool}|null,"scored":int,"mode":"grok|rules"}`
- executor: `{"trades":int,"lastSize":float|null,"stackPct":float,"state":"idle|executing|filled","progress":0-100,"name":str|null}`
- risk: `{"open":int,"guarding":bool,"last":"Trailed out|Stopped|Timed out|null","stopPct":f,"trailPct":f}`

`GET /api/chart?range=LIVE|1H|4H|1D|1W|ALL`
`{"points":[[ts,value],...],"start":float,"marks":[{"ts","kind":"buy|win|loss","label":"Bought X|+$N|-6% · -$N","mint"}]}`

`GET /api/activity?before=<id>` -> `{"rows":[activity row + "chain","image"],"next":id|null}` 30 per page.

`GET /api/coin/{mint}`
```
{"mint","chain","name","symbol","image","ageH":float,"price":float|null,"sinceEntryPct":float|null,
 "shield":"8/8"|"5/8"|null,"marketCap","liquidity","volume24h","holders":int|null,
 "candles":[[ts,o,h,l,c,v]] (1-minute, from db.prices),"position":{"sizeUsd","entryPrice","stopPrice",
 "trailPrice"|null,"trailActive":bool,"timeoutAt":ts,"pnlUsd","pnlPct","mode"}|null,
 "exitLine":float|null,"coinUrl":str,"grokhoodUrl":str|null,"safetyScore":int|null}
```
`GET /api/audit/{mint}` -> auditor JSON (latest) + `"name","symbol","chain","coinUrl"`, layers of the
latest audit; `{"layers":[]}` if none.
`GET /api/agents/{name}/decisions` -> `{"rows":[{"ts","mint","name","verdict","reason","coinUrl"}]}` last 20.
`GET /api/account` -> `{"mode","stack","handle","profileUrl"|null,"signupUrl","telegram":bool,"webpush":bool,
 "vapidPublicKey":str|null,"judge":"grok|rules","grokToday":{"calls","costUsd"}}`
`GET /api/settings` -> `config.all_settings()`; `POST /api/settings {"key","value"}` -> 200 `{"key","old","new"}` or 400 `{"error"}`.
`POST /api/agents/{name}/pause|resume`, `POST /api/mode {"mode","confirm":"LIVE"}`, `POST /api/push` (subscription JSON),
`POST /api/test-push`, `GET /api/search?q=` -> `{"rows":[{"mint","name","symbol","chain"}]}`,
`GET /api/stream?t=` SSE.

## Notifier (desk/notify.py)
`await notify.push(text: str, buttons: list[tuple[label, url_or_callback]], mint: str|None, kind: str)`.
Fans out to Telegram (if TG_TOKEN) and Web Push (db.push_subs). PAPER pushes start with "PAPER · ".
Callback buttons are `("Bought", "cb:bought:<signal_id>")`, `("Sold", "cb:sold:<position_id>")`, `("Skip", "cb:skip:<signal_id>")`.
Callback handling lives in `desk/actions.py` (`async def bought(signal_id)`, `sold(position_id)`, `skip(signal_id)`),
used by both Telegram and the API.
