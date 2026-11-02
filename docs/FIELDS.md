# FIELDS: raw keys of every external source

Every raw key the desk reads goes through `desk/fields.py`. Each row below was checked present in the
saved sample in `logs/raw/` (108 key paths checked by script on 2026-11-02, 0 missing). A key that is not
in the answer returns None, never a guess.

## PumpPortal `wss://pumpportal.fun/api/data`, `{"method":"subscribeMigration"}`
Sample: `pumpportal_migration.json` (4 real migrations, 2026-11-02 12:00 UTC).

| field | accessor | raw key |
|---|---|---|
| mint | `pp_mint` | `mint` |
| tx signature | `pp_signature` | `signature` |
| type | `pp_tx_type` | `txType` = "migrate" |
| AMM name | `pp_pool_kind` | `pool` = "pump-amm" (a name, NOT an address) |
| creator | `pp_creator` | **absent**: always None, creator comes from RugCheck `creator` |

The first frame is `{"message":"Subscribed to 'migration' events."}` and is skipped.

## DexScreener `GET /tokens/v1/{chain}/{addr,...}` (max 30 addresses)
Samples: `dexscreener_tokens_solana.json` (2 pairs 10 s old), `dexscreener_tokens_robinhood.json`.
Answer is a flat list of pairs.

| field | accessor | raw key |
|---|---|---|
| chain | `dex_chain` | `chainId` |
| dex | `dex_dex_id` | `dexId` ("pumpswap" on Solana graduates, "uniswap" on Robinhood) |
| pool address | `dex_pair_address` | `pairAddress` |
| token address | `pair_base_address` | `baseToken.address` (Robinhood: checksummed case, compare lowercased) |
| name / symbol | `dex_base_name` / `dex_base_symbol` | `baseToken.name` / `baseToken.symbol` |
| price USD | `dex_price_usd` | `priceUsd` (string) |
| liquidity USD | `dex_liquidity` | `liquidity.usd` (**missing on a pair a few seconds old**, seen in the solana sample) |
| volume | `dex_volume(p, w)` | `volume.{m5,h1,h6,h24}` |
| buys / sells | `dex_txns(p, w, side)` | `txns.{w}.buys` / `txns.{w}.sells` |
| change % | `dex_price_change(p, w)` | `priceChange.{m5,h1,h6,h24}` |
| market cap / fdv | `dex_market_cap` / `dex_fdv` | `marketCap` / `fdv` |
| pool created | `dex_pair_created_ms` | `pairCreatedAt` (ms) |
| image | `dex_image` | `info.imageUrl` |
| bundle for market.py | `pair_metrics` | priceUsd, liquidityUsd, marketCap, vol_m5, vol_h1, buys_m5, sells_m5, buys_h1, sells_h1, chg_m5, chg_h1 |

**Robinhood Chain on DexScreener: chain id `robinhood` works** (`/tokens/v1/robinhood/<addr>` returns the
Uniswap v4 pool, label "v4"). `robinhood-chain`, `robinhoodchain`, `rh`, `hood` return `[]`.
The pairAddress equals GROKHOOD `graduation.poolId` (Oak: 0xdb324dde...fad59). So price and liquidity for
Robinhood also come from DexScreener; GROKHOOD /chart prices are in the pair token (ETH/USDG) with
`usdPerQuote: null`, so no USD there.

## RugCheck `GET /v1/tokens/{mint}/report`
Samples: `rugcheck_report.json` (fresh graduate), `rugcheck_report_old.json` (GD52...pump, 4 h old).

| field | accessor | raw key |
|---|---|---|
| creator | `rc_creator` | `creator` |
| creator balance | `rc_creator_balance` | `creatorBalance` |
| token program | `rc_token_program` | `tokenProgram` (pump mints are Token-2022 `TokenzQd...`) |
| mint / freeze authority | `rc_mint_authority` / `rc_freeze_authority` | `token.mintAuthority` / `token.freezeAuthority` |
| supply / decimals | `rc_supply` / `rc_decimals` | `token.supply` / `token.decimals` |
| name / symbol | `rc_name` / `rc_symbol` | `tokenMeta.name` / `tokenMeta.symbol` |
| holders | `rc_total_holders` | `totalHolders` |
| top holders | `rc_top_holders`, `rc_holder_pct/owner/address` | `topHolders[].pct/.owner/.address` |
| markets | `rc_markets`, `rc_market_for_pool(rep, pool)` | `markets[].pubkey` (= DexScreener pairAddress) |
| market type | `rc_market_type` | `markets[].marketType` ("pump_fun_amm") |
| LP locked % | `rc_market_lp_locked_pct` | `markets[].lp.lpLockedPct` |
| LP locked USD | `rc_market_lp_locked_usd` | `markets[].lp.lpLockedUSD` |
| market liquidity | `rc_market_liquidity_usd` | `markets[].lp.baseUSD + lp.quoteUSD` |
| total liquidity | `rc_total_market_liquidity` | `totalMarketLiquidity` |

Creator's other launches (layer 9 Solana), samples `rugcheck_report_creator_tokens.json` (creator B92d..P9kF,
50 items, 20 in the last 7 days, 42 of 48 day-old coins under $5k) and `rugcheck_report_creator_tokens_old.json`
(creator D5D4..WnMx, 44 items, 0 in 7 days, 40 under $5k). Not in RugCheck's swagger; read off live reports.

| field | accessor | raw key |
|---|---|---|
| other launches | `rc_creator_tokens` | `creatorTokens` (list, newest first, current mint NOT included, max 50 seen; `null` = none) |
| mint | `rc_ct_mint` | `creatorTokens[].mint` |
| market cap USD | `rc_ct_market_cap` | `creatorTokens[].marketCap` (float USD: Gz7H.. 41,401 vs DexScreener 48,306 minutes later) |
| created | `rc_ct_created_at` | `creatorTokens[].createdAt` (ISO with nanoseconds, cut to microseconds) |

`null` vs missing: 23 fresh pump reports on 2026-11-02 all had the key; first-time creators had `null`
(Go nil slice), creators with history had a list. Missing key = no data. pump.fun's own
`frontend-api-v3.pump.fun/coins/user-created-coins/<creator>` answered 403 (Cloudflare challenge) from this
machine on every try, so it is not used. Dead = market cap under `auditor.dev_dead_mcap_usd` ($5,000): a pump.fun
coin starts at about 28 SOL = $2,630 at SOL $93.91 (2026-11-02), so under $5k means it never got to 2x its
launch value; the two serial creators above have median previous market caps of $205 and $33.

GD52 has 4 markets: pump_fun_amm 100% locked, orca 0, meteoraDlmm 0, meteora_damm_v2 0. That is why
layer 5 must use the market whose `pubkey` = the scanner pool, never "any market".

## RugCheck `GET /v1/tokens/{mint}/insiders/graph`
Sample: `rugcheck_insiders_graph.json` (GD52: 2 networks, 13 and 6 nodes). Fresh mints answer `[]`.

| field | accessor | raw key |
|---|---|---|
| networks | `rcg_networks` | top-level list |
| nodes | `rcg_nodes`, `rcg_node_id/participant/holdings` | `[].nodes[].id/.participant/.holdings` |
| links | `rcg_links`, `rcg_link_source/target` | `[].links[].source/.target` |

## Solana RPC (jsonParsed)
| field | accessor | raw key | sample |
|---|---|---|---|
| mint authority | `mint_authority` | `result.value.data.parsed.info.mintAuthority` | `rpc_getAccountInfo_mint.json` |
| freeze authority | `freeze_authority` | `...info.freezeAuthority` | same |
| supply / decimals | `mint_supply` / `mint_decimals` | `...info.supply` / `...info.decimals` | same |
| program owner of an account | `acct_program_owner` | `result.value[].owner` | `rpc_getMultipleAccounts_owner.json` (pool owner = `pAMMBay6...`) |
| token account owner | `ta_owner` | `account.data.parsed.info.owner` | `rpc_getProgramAccounts_delegated.json` |
| token account mint | `ta_mint` | `...info.mint` | same |
| amount (raw) | `ta_amount` | `...info.tokenAmount.amount` (string) | same |
| delegate | `ta_delegate` | `...info.delegate` (key only present when set) | same |
| delegated amount | `ta_delegated_amount` | `...info.delegatedAmount.amount` | same |
| owner balance | `token_balance` | `result.value[].account.data.parsed.info.tokenAmount.amount` | `rpc_getTokenAccountsByOwner.json` |
| getTokenLargestAccounts | `tla_address` / `tla_amount` | `address` / `amount` **NOT verified** | `rpc_getTokenLargestAccounts_429.json` |

getTokenLargestAccounts answered HTTP 429 on 12 tries over ~4 min on the public RPC, so its two keys follow
the RPC docs and are not proven. The code falls back to getProgramAccounts (verified) whenever it fails,
which is what actually runs on the public RPC today.

getProgramAccounts: Token program (`TokenkegQ...`) answers `[]` for pump mints (`rpc_getProgramAccounts_tokenkeg.json`),
Token-2022 answers every account (`rpc_getProgramAccounts_token2022.json`, first 5 of 3342 kept). Both are queried.
Pool / curve exclusion: a holder whose owner = pool address, or whose owner account is owned by the
PumpSwap program `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` (seen in sample) or the pump curve program
`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.

## GROKHOOD `https://api.grokhood.tech`
| field | accessor | raw key | sample |
|---|---|---|---|
| launches page | `ghl_items`, `ghl_next_cursor` | `items`, `nextCursor` | `grokhood_launches.json` |
| address / name / symbol / image | `gh_address/name/symbol/image` | `address`, `name`, `symbol`, `imageUrl` | same |
| deployer | `gh_deployer` | `deployer` | same |
| launched / graduated | `gh_launched_at` / `gh_graduated_at` | `launchedAt` / `graduatedAt` (ISO, null until graduation) | `grokhood_graduated_launches.json` |
| curve | `gh_curve_address`, `gh_curve_completed` | `curve.address`, `curve.completed` | `grokhood_token.json` |
| pool id | `gh_pool_id` | `graduation.poolId` (token card only, not in launches items) | same |
| Safety Score | `gh_score`, `gh_score_level` | `score.value`, `score.level` (score can be null on a brand new launch) | same |
| flags | `gh_flags` | `flags` (e.g. "graduated", "exempt_wallets") | same |
| ring | `gh_ring` | `ring` (null = none) | same |
| known operator | `gh_known_operator`, `gh_operator_links` | `knownOperator`, `operatorLinks` | same |
| dev sold | `gh_dev_sold`, `gh_dev_sold_pct` | `postLaunch.devSold`, `postLaunch.devSold.soldPct` | `grokhood_token_biaoqing.json` |
| post-launch | `gh_post_status`, `gh_insider_dump` | `postLaunch.status`, `postLaunch.insiderDump` | same |
| exempt wallets | `gh_exempt_count`, `gh_exempt_wallets` | `exemptCount`, `exemptWallets` | `grokhood_token.json` |
| signals | `gh_signal(t, id)`, `gh_signal_severity/value/reason` | `signals[].id/.severity/.value/.reason` (severity info/warn/danger) | same |
| holders | `ghh_items`, `ghh_wallet/share/sold/flags`, `ghh_total` | `items[].wallet/.share` (0..1 string)`/.sold/.flags`, `totalHolders` | `grokhood_token_holders.json` |
| clusters | `ghm_top_cluster`, `ghm_cluster_holders/wallets/share`, `ghm_cluster_count` | `summary.topCluster.holderCount/.walletCount/.share`, `summary.clusterCount` | `grokhood_token_map.json` |
| alerts | `gha_items`, `gha_level`, `gha_insider_sold_pct` | `items[].level` (warn/danger), `items[].insiderSoldPct` | `grokhood_token_alerts_warn.json` (Oak: `grokhood_token_alerts.json` = empty) |
| chart | `ghc_last_price`, `ghc_usd_per_quote`, `ghc_phase` | `last.price`, `usdPerQuote` (null), `phase` | `grokhood_token_chart.json` |
| X activity | `ghx_tracked`, `ghx_level`, `ghx_score`, `ghx_reasons` | `/x-activity`: `tracked` (bool), `level` organic/mixed/spam/none, `score` 0..100 (null when none), `reasons[]` | `grokhood_token_x_activity.json` (Oak: organic 95, 1 post) |
| X counts | `ghx_posts`, `ghx_authors`, `ghx_rug_promoters`, `ghx_graded_at` | `counts.posts`, `counts.authors`, `counts.rugPromoters`, `gradedAt` | same; `_noposts.json` (tracked, none, 0 posts), `_untracked.json` (`tracked:false`) |
| SSE | `ghs_address`, `ghs_score`, `ghs_flags` | `event: launch` (Launch item), `event: score` `{address,score,flags}`, `event: dump` (alert) | `grokhood_stream.txt` |

Endpoints: `/v1/launches?limit=&cursor=`, `/v1/tokens/:a`, `/v1/tokens/:a/holders`, `/v1/tokens/:a/map`,
`/v1/tokens/:a/alerts`, `/v1/tokens/:a/chart`, `/v1/tokens/:a/x-activity`, `/v1/scoring`, SSE `/v1/stream` (pings every 15 s).
`/map` and `/alerts` are per token, there is no global `/map` or `/alerts`.
There is no "graduation" SSE event: graduation shows as flag "graduated" on a `score` event and as
`graduatedAt` on launches, so the scanner also polls `/v1/launches` every scanner.poll_s.
X activity levels (GROKHOOD backend `src/shill/grade.ts`): score >= 70 organic, >= 40 mixed, else spam; none =
no graded posts. GROKHOOD's own Safety Score signal calls spam "spam-like or coordinated promotion" (penalty 15)
and mixed "part of it looks coordinated" (penalty 5). Live on 2026-11-02, the latest 300 launches had 16 organic,
26 none (tracked, no posts), 258 not tracked; no live spam/mixed sample, so those two follow the backend source.
`/x-activity` may run an on-demand X search server side (own TTL and budget); 404 = unknown token.
Only 6 of the latest 1500 launches had graduated (2026-11-02).
Display-only, not verified (ring/knownOperator were null in every sample): the `id` inside a non-null
`ring` / `knownOperator` object; the layer decision only uses null vs not null.

## STEP-2 probe (`python scripts/probe.py`, 2026-11-02, public RPC)
```
rpc: https://api.mainnet-beta.solana.com
1 getSlot: 464493066 (1990 ms)
graduated mint: 8s4bNqEztH1JP7mkU747uMr7AckCQdiV47FDqYnDpump
2 DexScreener: 1 pairs (348 ms), best pumpswap pool HcGPz2eXV8SFcMVQ9fJPBd71bRLJVzUtd2avvv86GwbK liq $170,107 h1 -10.23% metrics {'priceUsd': 0.00366, 'liquidityUsd': 170106.7, 'marketCap': 3644728.0, 'vol_m5': 6638.76, 'vol_h1': 126599.32, 'buys_m5': 431, 'sells_m5': 197, 'buys_h1': 5938, 'sells_h1': 2864, 'chg_m5': 0.07, 'chg_h1': -10.23}
3 RugCheck report (368 ms): creator AoEEgeHgLMbANGd2zyRH8wTy2ekvxFNduSTFTxuVydeF mintAuthority None freezeAuthority None holders 9357 markets 3 pool LP locked 100.0%
  insiders graph: 0 networks
4 getProgramAccounts: 9358 token accounts (8736 with balance, 1 with a delegate) in 1302 ms. Layer 8 works on this RPC.
  top 10 (pools/curve excluded): 0.67% excluded owners ['HcGPz2eXV8SFcMVQ9fJPBd71bRLJVzUtd2avvv86GwbK']
5 GROKHOOD launches: 5 items, newest MINEHOOD score None
```
getProgramAccounts on the public mainnet-beta RPC answered for pump (Token-2022) mints: 3342 accounts for
GD52, 9358 for 8s4b, about 1.3 s and 2 MB each. Layer 8 works on it today. If desk.rpc_url ever points at an
RPC that refuses it (publicnode: "Indexed requests require a personal token"; drpc free: chain not available),
layer 8 goes red "no data: delegations" until desk.rpc_url points to an RPC that allows getProgramAccounts
(e.g. Helius).
