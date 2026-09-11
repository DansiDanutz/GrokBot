# GrokBot autopilot specification (Phases A, B, C)

Purpose: let GrokBot paper trade KuCoin USDT perpetual grids on its own, driven by
the hourly radar, and let Dan track it in real time. This is the base specification
Codex implements after the Phase 0 finish sequence. It is the single source of
truth for the autopilot; the real-time addendum is folded in below.

Ground rules: never place real orders. No CoinGlass, no backtest or replay machinery,
no ML. Do not touch `~/Sandbox/grokbot/zmarty-paper-runtime`, any installed launchd
agent, `~/.openclaw`, `~/.claude`, `~/.paperclip`. Pure functions and immutable
state (return new dicts, never mutate). unittest, no network in tests, fixtures under
`tests/fixtures`. One PR per phase, based on the previous phase's merged head.
Every PR: conventional commits, `npm run verify` and `npm run verify:secrets` green,
frozen head posted on issue #16, then idle until Claude's gate comment.

Inputs already in the repo: `trader/radar` (hourly `radar.json` with rows carrying
`symbol, direction, price, range_low, range_high, step_pct, grids,
expected_grids_per_hour, rank_score, funding_pct, atr_1h_pct, low_7d, high_7d`),
the market SQLite (`klines`, `ticker_snapshots`, `top_of_book`, `universe`,
`funding`), `paper_grid/server.py` (local dashboard, 127.0.0.1),
`paper_grid/publish_vercel.py` (static publisher), `paper_grid/csp.py`,
`paper_grid/radar.html` (design and CSP pattern to copy).

## Phase A: paper grid engine (`trader/papergrid/`)

Pure functions, no I/O.

`open_bot(spec, price, now_ms) -> bot`
- spec: `{bot_id, symbol, direction: LONG|SHORT|NEUTRAL, range_low, range_high,
  step_pct, grids, notional_usdt, leverage, funding_pct}`.
- Grid lines are geometric from `range_low` to `range_high`, `grids + 1` lines.
- Initial resting orders like KuCoin Futures Grid: LONG holds buys on every line
  below price; SHORT holds sells on every line above price; NEUTRAL holds buys
  below and sells above. Contracts per line = `notional_usdt * leverage / grids / price`.
- Initial position: LONG seeds one line of long inventory per resting sell order,
  SHORT seeds one line of short inventory per resting buy order, NEUTRAL none.
  The empty line contributes no inventory. Entry fee applies.

`step(bot, tick_or_candle) -> (bot, events)`
- Tick `{ts_ms, price}`: a resting buy at line L fills when `price <= L`; a resting
  sell fills when `price >= L`. Candle `{ts_ms, open, high, low, close}`: buy fills
  when `low <= L`, sell when `high >= L`; when both sides could fill in one candle,
  process in the order open -> extreme nearest to open -> other extreme -> close.
- On a fill, place the paired order one line up (after a buy) or one line down
  (after a sell). A completed grid is counted only when a paired fill reduces the absolute net position.
- Fee: `FEE_RATE = 0.0006` of fill notional per fill.
- Funding: every 8 h boundary (00:00, 08:00, 16:00 UTC) apply
  `position_notional * funding_pct / 100`, sign by side.
- Track: `fills, completed_grids, realized_pnl, unrealized_pnl, position_contracts,
  avg_entry, fees_paid, funding_paid, equity, peak_equity, max_drawdown_pct,
  last_price, last_ts_ms`.
- Emits, never acts: `RANGE_BREAK` when price is beyond the range by more than one
  step for 3 consecutive updates; `STOP_LOSS` when
  `(realized + unrealized - fees - funding) < -0.12 * notional_usdt`.

`close_bot(bot, price, now_ms, reason) -> (bot, events)`: flatten at price with
fee, cancel resting orders, freeze final stats, `reason` from the enum
`LABEL_FLIP | RANGE_BREAK | STOP_LOSS | DROPPED | MAX_AGE | MANUAL`.

Tests: fixtures with 200 synthetic 1m candles (oscillation, trend up, trend down)
and a tick series; assert grids counted, PnL sign, fees, funding, RANGE_BREAK,
STOP_LOSS, deterministic replay, and no mutation (deepcopy compare of inputs).

## Phase B: autopilot daemon (`trader/autopilot/`)

CLI: `python3 -m trader.autopilot run --database <sqlite> --state <state.json>
--radar <radar.json> --snapshot <autopilot.json> [--telegram-chat-id
--telegram-state]`. Single loop, KeepAlive under launchd, clean SIGTERM, one
instance lock file. Also `python3 -m trader.autopilot once ...` for tests and
manual runs (one tick pass + one decision pass, then exit).

Constants (one module, `trader/autopilot/constants.py`):
`PAPER_EQUITY_USDT = 10_000`, `MAX_BOTS = 6`, `NOTIONAL_PER_BOT_USDT = 1_000`,
`SLOTS = {NEUTRAL: 2, LONG: 2, SHORT: 2}`, `DIRECTION_CAP = 4`,
`LEVERAGE_TREND = 3`, `LEVERAGE_NEUTRAL = 5`, `STEP_NEUTRAL_PCT = 0.45`,
`NEUTRAL_RESERVE_USDT = 200`, `MAJORS_MAX = 1`, `MOVERS_MAX = 1`,
`MIN_EXPECTED_GRIDS_PER_HOUR = 2.0`, `COOLDOWN_HOURS = 6`, `MAX_AGE_HOURS = 72`,
`TICK_INTERVAL_S = 10`, `DECISION_INTERVAL_S = 300`, `SNAPSHOT_MAX_INTERVAL_S = 30`,
`TICK_STALE_ALERT_S = 180`, `KUCOIN_DOWN_ALERT_S = 300`.

- `MOVERS_MAX`: max trend-profile bots whose radar source section is `movers`.
- `MAJORS_MAX`: max bots on major symbols, any profile.
- Neutral-profile bots sourced from `movers` fill NEUTRAL slots and are exempt from `MOVERS_MAX`.

Tick loop (every 10 s): one KuCoin public allTickers call; keep open-bot symbols
plus BTC/ETH/SOL. Feed each open bot `step(bot, tick)`. Update live mark,
unrealized PnL, portfolio equity, drawdown. Write state and snapshot atomically
after any tick that changed something, otherwise at most every 30 s with a fresh
`generated_at_ms`.

Decision pass (every 5 min, and whenever `radar.json` mtime changes):
- Close first: on `RANGE_BREAK` or `STOP_LOSS`; when the symbol's radar label
  flips against the bot (LONG bot and label SHORT or TURNING-DOWN; SHORT bot and
  label LONG or TURNING-UP; NEUTRAL bot and label LONG or SHORT, unless that is the very label the bot was opened with: a neutral grid on a trending mover closes only when the trend label changes); when the symbol
  is absent from the radar for 2 consecutive scans (`DROPPED`); after
  `MAX_AGE_HOURS` (`MAX_AGE`). Closed symbol enters cooldown.
- Then open: allocate `MAX_BOTS = 6` across LONG, SHORT and NEUTRAL slots, two
  each. LONG takes turning_up then long rows; SHORT takes turning_down then short;
  NEUTRAL takes neutral then movers rows, movers ordered by highest atr_1h_pct.
  Rank candidates by rank_score within each non-mover section. Skip open symbols,
  cooldowns and candidates below MIN_EXPECTED_GRIDS_PER_HOUR; keep MAJORS_MAX and
  MOVERS_MAX. If a direction has no qualifying candidate, its free slot goes to
  NEUTRAL first, then to the other trend side, never exceeding four of one direction.
  Rebalance only when a slot frees up; never close a bot merely to rebalance.

### Phase B direction mix and profiles (Dan's amendment)

- TREND (LONG/SHORT): radar range and step unchanged (0.8%, or 0.52% for majors),
  leverage 3, notional 1,000 USDT.
- NEUTRAL: `ATR4h = price * atr_4h_pct / 100`,
  `range_low = min(low_7d, price - ATR4h)`,
  `range_high = max(high_7d, price + ATR4h)`, step 0.45%, leverage 5,
  notional 1,000 USDT plus 200 reserve counted in bot equity but not deployed.
  Grid count is `round(ln(high/low) / ln(1 + step_pct/100))`, capped at 200.
  The profile is modeled on Dan's RAY Neutral 5x bot (1.10–2.00, 140 grids,
  18.6 observed grids/hour). STOP_LOSS stays 12% of notional; RANGE_BREAK remains
  three consecutive updates beyond the range by more than one step.
- Share k(step) between radar and autopilot: standard coins use
  `1.9 * sqrt(step_pct / 0.8)`, majors (turnover >=50M USDT) use
  `0.45 * sqrt(step_pct / 0.52)`. Expected grids/hour is k(step)*ATR1h%/step%.
  Assert RAY with step 0.43% and ATR1h 6.2% is within **17–21 grids/hour**.
- Open and closed bots are grouped by direction, with per-direction totals:
  bots, completed grids, grid profit, unrealized, fees, funding and true net.
  Every bot row includes grids/hour since opening. Daily 08:00 Europe/Bucharest
  Telegram summary puts LONG, SHORT and NEUTRAL on separate lines with grids/hour
  and net PnL. Phase C uses the same grouped data on the paper page.
- Tests: balanced eligible radar opens 2/2/2; only-long trend candidates open at
  most four LONG and fill remaining slots with NEUTRAL from movers; freed SHORT
  slot refills with SHORT when available.
- Test that an additional movers candidate as a LONG bot is refused when
  MOVERS_MAX is already reached while neutral-profile movers remain eligible.

Restart backfill: on start, replay 1m candles from the market DB from each bot's
`last_ts_ms` to now (add 1m klines ingestion to the data layer for open symbols,
last 6 h, via the existing KuCoin public REST client), then switch to ticks.
Rerunning on the same data produces no new fills (idempotent).

State file (`--state`): atomic write, 0600, `schema_version`, `open_bots`,
`closed_bots` (keep 30 days), `equity_curve` (sampled once per minute, keep 30
days), `cooldowns`, `radar_seen` (last two scan ids per symbol). Never contains
a token.

Events log: append-only JSONL at `<runtime>/autopilot/events.jsonl`, daily
rotation, keep 30 days. Types: `OPEN, FILL, GRID, CLOSE, RANGE_BREAK, STOP_LOSS,
ALERT, RECOVER, ERROR`. Fields: `ts_ms, type, bot_id, symbol` plus numbers only.

Health in the snapshot: `heartbeat_ms, tick_age_s, kucoin_ok, radar_age_min`.
Telegram alert once when no tick for `TICK_STALE_ALERT_S` or `kucoin_ok` false
for `KUCOIN_DOWN_ALERT_S`, once on recovery.

Telegram (token via `DLS_TELEGRAM_BOT_TOKEN` from `scripts/credential-exec.py`,
same as the radar): one message per open and per close (symbol, direction, range,
grids done, PnL, reason), daily 08:00 Europe/Bucharest summary (equity, open
bots, best and worst closed bot of the day), health alerts as above.

Tests: fixture `radar.json` plus fixture ticks and candles; assert open selection
order, `MAX_BOTS`, majors and movers caps, cooldown, each close rule, tick fill
semantics, restart backfill, idempotency, snapshot atomicity (no partial file
readable mid-write), state file free of tokens.

## Phase B amendment: dynamic two-tier watchlist (Dan)

This amendment supersedes the earlier six-bot admission rule. Implement in Phase B;
render in Phase C. No real orders or deployment is authorized.

### Shared radar score

Score every radar row from 0 to 100 in `trader/radar`, shared by radar and autopilot.
Each row carries `score` and `score_parts: [{code, value, points}]`; value is the
measured number used by the rule. No free text in the data; the page maps codes to
sentence templates and fills the numbers.

- OSCILLATION, weight 30: expected_grids_per_hour scaled 0..30, reaching 30 at
  >=20 grids/hour.
- TREND_CLARITY, weight 20: LONG/SHORT with daily and 4h agreeing =20; TURNING =14;
  NEUTRAL with price in the middle 25..75% of the 7d range =16; otherwise 6.
- LIQUIDITY_TURNOVER: value is 24h turnover in USDT; 0 points at <=3M,
  linear to 10 at 30M, capped at 10 above.
- LIQUIDITY_SPREAD: value is spread in percent; 5 points at <=0.05%,
  linear to 0 at 0.15%, 0 above. Each code gets its own Phase C template.
- ROOM, weight 15: distance to the nearest range edge in ATR4h units, scaled
  0..15, reaching 15 at >=2 ATR.
- FUNDING, weight 10: 10 when funding favours the bot side (short and positive,
  long and negative); 5 when absolute rate <0.01%; 0 against.
- STABILITY, weight 10: 10 when atr_1h / (atr_4h / 2) is between 0.6 and 1.4;
  4 outside.
- Penalties are separate reason codes: MOVER_RISK -15 when absolute change_24h
  >30%; YOUNG_LISTING -10 when listed <14 days; STALE_DATA -20 when snapshot
  age >60 minutes; MAJOR_LOW_YIELD -10 on BTC/ETH/SOL.

### Persistent core and bench

Constants: `CORE_SIZE=5`, `BENCH_SIZE=5`, `PROMOTION_MARGIN=10`,
`CORE_MIN_HOLD_HOURS=2`, `MAX_SWAPS_PER_SCAN=1`, `MAX_BOTS=CORE_SIZE=5`.
Persist core and bench in state and snapshot. Each entry is
`{symbol, direction, score, score_parts, since_ms, rank}`.

Every hourly radar scan, bench is the five best-scoring qualifying rows not in
core. Then at most one swap: the top bench coin replaces the lowest-scoring core
coin if its score is at least core_score + PROMOTION_MARGIN and the core coin has
been in core for at least CORE_MIN_HOLD_HOURS. A core coin absent from the radar
entirely (fails filters two scans in a row) is removed regardless, and the top
bench coin fills the seat. Same-symbol direction changes update the entry in
place with a DIRECTION_CHANGE event. Cold start: core is the top five by score.

Every swap writes a WATCHLIST event with
`{ts_ms, type: PROMOTE|DEMOTE|DROP|DIRECTION_CHANGE, symbol, score,
replaced_symbol, replaced_score, margin}`. Keep the last 48 in the snapshot as
`watchlist_history`.

Bots open from core only. The direction slots (2 NEUTRAL, 2 LONG, 2 SHORT,
cap 4 of one kind) are preferred slots, borrowing up to 4 of one direction.
Fill preferred slots first; lend an unavailable direction’s slots to NEUTRAL
first, then the other trend side. Total MAX_BOTS is five, one bot per symbol.
With five LONG core coins, open four LONG and leave one seat empty. A demoted coin's open bot keeps running
under its own close rules (label flip, range break, stop, max age); it is just
not reopened. Bench coins never get a bot.

### Telegram and Phase C rendering

Telegram sends one message per hourly scan only when core or bench changed:
"Core: SYM dir score (top reason code) …", then "Bench: …", then the swap line with
both scores and the margin. Daily summary adds the number of swaps.

Phase C adds a Watchlist section above the bots on `/paper`: Core and Bench
columns, one card per coin with symbol, direction chip, a 0..100 score bar, small
labelled score-part bars with measured values, and a why-list built from code
templates. Examples: OSCILLATION "18.4 expected grids/h", ROOM "1.6 ATR to the
nearest edge", MOVER_RISK "up 31% in 24h, hit-and-run". Below, display the last
24 watchlist events (for example "RAY promoted over SAGA, 71 vs 52"). Same CSP
rules; sentence templates live in the page script, only codes and numbers in JSON
apart from the prescribed symbol/direction fields.

Tests: known-number score fixtures; promotion margin and minimum-hold hysteresis;
at most one swap per scan; drop after two missed scans; no bots from bench;
watchlist and history in snapshots; Telegram silent when nothing changed.

## Phase C: reporting, `/paper` page, LaunchAgent

`paper.html` is a NEW public page, served at `/paper`, next to `/radar`.

- `paper_grid/public_snapshot.py`: allowlisted `_autopilot(source)` DTO: `equity,
  change_24h_pct, change_7d_pct, open_bots[]` (bot_id, symbol, direction, price,
  range_low, range_high, grids, completed_grids, realized_pnl, unrealized_pnl,
  fees_paid, funding_paid, opened_ms), `closed_bots[]` (last 20, same fields plus
  closed_ms and reason from the enum), `equity_curve` ([ts_ms, equity] pairs, 30
  days, downsampled to at most 2 000 points), `totals` (bots, grids, pnl, fees),
  health fields. Numbers validated like `_radar`; the only strings are symbol,
  direction, reason.
- `paper_grid/server.py`: `/paper` route (dashboard, nonce CSP), `/api/autopilot`
  (snapshot), `/api/events?since=<ts_ms>` (up to 500 events from the JSONL,
  numbers validated, type from the enum). Same symlink check and 2 MiB cap as
  `/api/radar`. Bind stays 127.0.0.1; the RUNBOOK documents `tailscale serve`
  for phone access (Dan runs it).
- `paper_grid/publish_vercel.py`: stage `paper/index.html` from `paper.html` and
  `data/autopilot.json`; CSP routes `/paper` and `/paper/(.*)` via
  `csp.static_policy`. Publisher interval 5 min in the plist example only.
- `paper_grid/paper.html`: same design system and CSP rules as `radar.html` (one
  inline style, one inline script, DOM APIs and textContent only, no external
  resources). Fetch `/api/autopilot` then `/data/autopilot.json`. Poll
  `/api/autopilot` and `/api/events` every 10 s when the local API answers;
  render without flicker (diff by bot_id). Status pill: LIVE when
  `tick_age_s < 30`, DELAYED under 180, STALE otherwise; on Vercel show
  "snapshot, published <n> min ago". Sections: equity headline with 24 h and 7 d
  change; open bots table (symbol, direction chip, range bar with price tick,
  grids done, PnL, age); closed bots table; equity curve as inline SVG built with
  `createElementNS`; live event feed (last 50); per-bot PnL sparkline. Link
  `/paper` from the radar nav and `/radar` from this page.
- `config/launchd/com.danslab.trader-autopilot.plist.example`: KeepAlive daemon
  through the credential-exec wrapper, placeholder chat id, RunAtLoad false, not
  installed.
- `docs/STATUS.md`: rewrite "what GrokBot is" and the done table; add a RUNBOOK
  with exact install steps for radar and autopilot LaunchAgents, the tailscale
  serve command, and the publisher cutover from the v1 checkout.

### Phase C amendment: landing page and publisher cutover (Dan)

Make `/paper` the site landing page. `publish_vercel.py` stages
`paper/index.html` also as `index.html`, retains `/radar`, and publishes the v1
dashboard at `/control`. Home navigation links Paper, Radar and Control. Update
CSP routes and `test_publish_vercel.py` for these destinations. The watchlist
cards and last 24 watchlist events described above render in Phase C, with code
templates for every score part in the page script and the same CSP as radar.html.

Add `config/launchd/com.danslab.trader-publisher.plist.example`: run
`paper_grid/publish_vercel.py` from the GrokBot checkout every five minutes through
the credential-exec wrapper. Read radar and autopilot snapshots from the GrokBot
runtime directory and the v1 report from the existing v1 runtime directory
read-only. Do not install the template or modify any installed launchd agent.

The `docs/STATUS.md` RUNBOOK must contain exact manual cutover steps for Dan:

1. Boot out the current `com.danslab.trader-publisher`.
2. Install radar, autopilot and publisher plists from the three examples, using
   his chat id where required.
3. Bootstrap them; configure `tailscale serve` for the local dashboard.
4. Verify `/`, `/radar` and `/paper` on the live site with curl and tail all three
   logs.
5. Roll back by booting out the new publisher and bootstrapping the old plist.

These are documented operator actions only; Codex does not execute them. Phase B
and Phase C each retain their own PR and frozen-head audit on issue #16. Remain
idle after each handoff until Claude's gate. After the Phase C gate, merge, post
default head, test count, all three plist paths and Dan's manual steps on #16,
then close #16.

After the Phase C gate: merge, post the final handoff on #16 (default branch
head, test count, both plist paths, the manual steps for Dan), close #16.

_Last verified: 2026-09-11_

## Phase A accounting

## Answer: Phase A accounting rules (copy verbatim into `docs/autopilot-spec.md`, section "Phase A accounting", first commit of the Phase A PR)

### A1. Position, orders and pair matching

- **One signed net position per bot** (`position_contracts`, positive long, negative short) with a volume-weighted `avg_entry`, the way KuCoin Futures Grid holds one position. No per-pair inventory.
- **One resting order per grid line, and exactly one empty line at all times.** At open the empty line is the line nearest the price. Below it every line holds a buy, above it every line holds a sell (NEUTRAL); LONG holds buys below and sells above plus the seeded long inventory; SHORT mirrored.
- **Fill rule.** A fill consumes the order on line `i` and places the counter-order on the adjacent line toward the fill direction, which is by construction the empty line: after a buy on `i`, a sell on `i+1`; after a sell on `i`, a buy on `i-1`. Line `i` becomes the new empty line. An update that crosses several lines processes them one at a time in price order, each at its own line price (paper limit fills, never at the tick price).
- **Two ledgers, both updated on every fill:**
  - *Position ledger:* every fill updates `position_contracts` and `avg_entry`. When a fill reduces `|position|`, `realized_pnl += closed_contracts * (fill_price - avg_entry) * side_sign`. When it increases `|position|`, `avg_entry` is re-weighted. Fees and funding are separate fields and are never mixed into `avg_entry`.
  - *Grid ledger:* every resting order carries `paired_line` (the line index of the fill that placed it). A fill of an order with `paired_line` set counts one `completed_grid` and adds `contracts * |line_price − paired_line_price|` to `grid_profit` **only when that fill reduces `|position_contracts|`** (a closing fill). A paired fill that increases `|position|` opens a new pair and counts nothing. Seeded inventory counts as an open pair, so a seeded sell on a LONG bot (or seeded buy on SHORT) is a closing fill and counts. Seeded orders carry `paired_line = the adjacent line toward the open price` (a seeded sell on `k` pairs with `k-1`, a seeded buy on `k` pairs with `k+1`), so a seeded fill also counts a grid; the difference between that adjacent line and the actual open price shows up in the position ledger, not the grid ledger.
  - `grid_profit` is the KuCoin "Grid Profit" figure; `realized_pnl + unrealized_pnl - fees_paid - funding_paid` is the bot's true net. They are reported side by side, never summed together.
- **Seeded inventory for LONG/SHORT:** bought (or sold) at the open price as one taker fill with fee, size `contracts_per_line * len(seeded orders)` (resting sells for LONG, resting buys for SHORT); the empty line contributes no inventory; `avg_entry = open price`.
- **Close:** cancel all resting orders, flatten at the close price with fee, realize through the position ledger. No grid is counted on close.

### A2. Funding valuation and ordering

- Funding is charged **at each 8 h boundary (00:00, 08:00, 16:00 UTC)** on the **position as it stands before the update that crosses the boundary**, valued at `last_price` (the last price seen before the boundary): `funding_paid += |position_contracts| * last_price * funding_pct / 100 * side_sign`, where longs pay when the rate is positive.
- **Ordering inside one update:** funding first (on the pre-update state), then the fills of that update, then RANGE_BREAK / STOP_LOSS evaluation.
- **Missed boundaries** (daemon down, restart backfill): process the backfill candles in time order; at each boundary between two candles use the position after the earlier candle and its close as `last_price`. If no candle exists across a boundary, the position is unchanged and `last_price` is the last known price. Every boundary since `last_ts_ms` is charged exactly once; `last_funding_ts_ms` on the bot makes this idempotent.
- **Rate:** Phase A uses `funding_pct` from the bot spec. `step(bot, update, *, funding_pct=None)` accepts an override so Phase B can pass the latest rate from the market DB `funding` table at each boundary.

### Tests to add for these rules
Multi-line jump fills in order; seeded fill counts a grid and the open-price gap lands in realized; grid_profit vs net never double counted; funding charged once per boundary across restart with and without candles; ordering funding → fills → stops inside a boundary-crossing update.

Codex: no other question is open. Copy this into the spec, implement, post the frozen Phase A head here and stay idle for the gate.



### Conditional-gate regression fixture

For RAYUSDTM on 11 September 2026, 08:20–15:20 UTC, retain the 418 stored
one-minute candles in `tests/fixtures/papergrid/ray_20260911.json`. Neutral 5x,
range 1.10–2.00, 140 grids, 1,000 USDT: assert `130 <= completed_grids <= 200`
and `20 <= grid_profit <= 32`. This is the bounded A-1 regression authorized
by issue #16 comment 5637070434, not a research sweep.


## Dan correction — uniform 5× and immediate range exit (11 September 2026)

This section supersedes the earlier TREND 3× profile and delayed range-break rule.
All new paper bots use 1,000 USDT margin, 5× leverage and a separately allocated
200 USDT reserve. Five bots allocate 6,000 of the 10,000 USDT bankroll; reserve
is not leveraged. Existing incompatible profiles are closed with PROFILE_UPDATE
and replaced through normal Core admission, recording fees and PnL rather than
rewriting historical fills. No performance history is reset.

At the first observed price at or beyond either configured range boundary, the
autopilot closes that paper bot on the same update at the observed price, with
RANGE_BREAK. It does not wait three updates, an extra grid step, or the five-minute
decision pass. The existing 12% loss protection is also closed on its triggering
update. Backfill candles with a boundary excursion close at the first outside
price in the engine's deterministic OHLC path, without inventing boundary-price
execution or subsequent fills. Price gaps may create losses beyond the threshold.

Liquidation output must distinguish a modeled isolated/net-position estimate
from an exchange-confirmed Futures Grid hedge-mode liquidation price. Use
verified public maintenance-margin parameters, show their freshness and fee
assumptions, and report both current margin and the additional-reserve scenario.
Missing parameters or flat inventory must be labeled explicitly. A range stop
does not guarantee avoiding liquidation. No real exchange orders are authorized.


## Dan clarification — stop-loss is the range boundary

Supersedes the separate 12% loss trigger in the paper autopilot: Long stop-loss
is range_low, Short stop-loss is range_high, and Neutral exits at either edge.
Any first observed touch or crossing of a range edge closes on that update;
the opposite edge remains a range exit. No range is widened after entry.

New radar setups must identify a usable structural level from completed hourly
candles over the last 7 days. Confirm pivots with two candles either side; cluster
levels within min(0.25 ATR1h, 0.5% price), require two distinct tests separated
by at least 3 hours. Pick nearest confirmed support below price and resistance
above price. Two completed closes beyond an old resistance/support allow the
level to change roles. Long uses support as its lower bound, Short resistance as its upper,
Neutral requires both. Unverified rows cannot open a bot; legacy snapshots missing
the verification flag cannot admit new bots. This is a deterministic heuristic,
not a claim that support cannot break. RAY 1.40 is Dan’s observed example, not a
universal hard-coded limit. Existing bots retain their entered ranges and are
labeled legacy until they close normally.
