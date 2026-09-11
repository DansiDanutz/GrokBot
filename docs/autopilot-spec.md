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
`LEVERAGE = 3`, `MAJORS_MAX = 1`, `MOVERS_MAX = 1`,
`MIN_EXPECTED_GRIDS_PER_HOUR = 2.0`, `COOLDOWN_HOURS = 6`, `MAX_AGE_HOURS = 72`,
`TICK_INTERVAL_S = 10`, `DECISION_INTERVAL_S = 300`, `SNAPSHOT_MAX_INTERVAL_S = 30`,
`TICK_STALE_ALERT_S = 180`, `KUCOIN_DOWN_ALERT_S = 300`.

Tick loop (every 10 s): one KuCoin public allTickers call; keep open-bot symbols
plus BTC/ETH/SOL. Feed each open bot `step(bot, tick)`. Update live mark,
unrealized PnL, portfolio equity, drawdown. Write state and snapshot atomically
after any tick that changed something, otherwise at most every 30 s with a fresh
`generated_at_ms`.

Decision pass (every 5 min, and whenever `radar.json` mtime changes):
- Close first: on `RANGE_BREAK` or `STOP_LOSS`; when the symbol's radar label
  flips against the bot (LONG bot and label SHORT or TURNING-DOWN; SHORT bot and
  label LONG or TURNING-UP; NEUTRAL bot and label LONG or SHORT); when the symbol
  is absent from the radar for 2 consecutive scans (`DROPPED`); after
  `MAX_AGE_HOURS` (`MAX_AGE`). Closed symbol enters cooldown.
- Then open: candidates by `rank_score` from sections in the order turning_up,
  long, turning_down, short, neutral, movers; skip open symbols, cooldowns, rows
  with `expected_grids_per_hour < MIN_EXPECTED_GRIDS_PER_HOUR`; respect
  `MAJORS_MAX` and `MOVERS_MAX`; open until `MAX_BOTS`. Bot spec comes straight
  from the radar row.

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
