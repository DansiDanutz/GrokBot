# Self-learning upgrade: grid-yield-first research loop

Prepared 2026-09-12 by Claude (branch `claude/self-learning-loop`).
Executor: Codex, per docs/grokbot-roadmap-audit-protocol.md (branch + PR;
Claude audits the fixed head). Owner and only person who may enable live
trading or spend money: Dan.

Goal: find better entry/exit timing **day by day** by analyzing wrong moves,
wrong trades, and wrong entries, using technical analysis, liquidation
history, and patterns. **GRIDS are the optimization target.**

## Non-negotiable constraints (unchanged by this document)

1. The trading method stays the KuCoin leverage **grid bot**, paper-only.
   No live orders, ever, from this roadmap.
2. Coin selection stays the radar advisory ranking. Nothing here may hard
   override Dan's radar scoring; learned rules only gate entry timing.
3. Never touch the v1 evidence run or the active ZmartyChat-paper-grid
   checkout. New runtime surfaces get their own launchd labels.
4. Every learned change passes the existing evidence gates (anti-noise
   minimums, fail-closed schema, reversible auto-apply, proposals ledger).
5. TDD, small commits, conventional commits, `npm run verify` green; no
   secrets in the repo; files < 800 lines.

## North-star metric

**Grids per bot-hour** (and grid profit per bot-hour), measured per entry.
Not total PnL: total PnL rewards directional luck, which we do not trade.
A trade that produced many cheap grids then exited at break-even is a good
entry. A trade that made money on direction but few grids is noise we do
not want to learn from.

## Verified current state (2026-09-12)

- DECISION events carry full entry features (radar_score,
  expected_grids_per_hour, range_width_pct, funding_rate, rule_blocks);
  62 decisions, 20 opens, 16 closes, 428 grid events so far.
- 07:00 daily review correlates outcomes at trend-bucket level, includes a
  liquidation-aware 6 h post-exit counterfactual, and writes PROPOSAL-grade
  learnings. Tier-1 rules (16-18) auto-apply behind
  MIN_DAILY_CLOSED=5, MIN_CUMULATIVE_CLOSED=20, MIN_RULE_BENEFIT_USD=10,
  MAX_AUTO_PER_DAY=3, COOLDOWN_DAYS=7.
- Phase-2 market sqlite: ~24.8M rows (klines/funding/OI/book/tickers).
- `coinglass_liquidations` table existed with **zero rows** — the live burst
  filter analyzed history then discarded it. Phase A below fixes this.

## Phase A — entry-outcome ledger (implemented scope: none yet)

Join DECISION → OPEN → GRID/FILL stream → CLOSE into one durable per-entry
outcome record (jsonl under `~/Sandbox/grokbot/autopilot/`), written when a
position closes. Fields: entry features snapshot, hold hours, grids counted,
grid profit, fees, funding, exit reason, counterfactual outcome, and a
classification:

- `LOW_YIELD` — grids per bot-hour < 50% of the radar expectation baked
  into the entry decision.
- `RANGE_BREAK_EARLY` — risk close (RANGE_BREAK/STOP_LOSS) with fewer grids
  than the fee-adjusted seed cost.
- `YIELD_KILLER` — funding + taker fees ≥ 30% of grid profit.
- `HEALTHY` — none of the above.

The ledger is the training set for every later phase; the daily review links
its bucket analysis to these classes. Acceptance: closing any paper position
appends exactly one ledger record; offline tests cover each class and the
join (one entry, many grids); dashboard surfaces the class mix per day.

## Phase B — CoinGlass history persistence (IMPLEMENTED on this branch)

`trader/data/coinglass_history.py` records completed aggregate-exchange
hourly liquidation USD (Binance, OKX, Bybit) into the existing
`coinglass_liquidations` table, idempotently, with a `data_quality` record
per run (`check_name='coinglass_history'`). Public provider data only; the
live entry burst filter is unchanged. Urgent because provider history is a
short rolling window and cannot be backfilled cheaply — every day not
recording is a day of clusters lost.

Run hourly against the Phase-2 workspace db, e.g.
`python3 -m trader.data.coinglass_history --database <db> --once`
(symbols default to the top active universe by 30d turnover; CoinGlass
request cap 9 symbols per run). Deployment stays a reviewed `.example`
launchd file per repo rule. Acceptance tests: offline getter injection,
idempotent upsert, per-symbol failure tolerance, fail-closed missing key.

## Phase C — regime gate: ACTIVE / SELECTIVE / WAIT

The one strategic addition (credit: Kimi's proposal, 2026-09-12): make
"when to do nothing and let running bots top grid" an explicit system state.

Inputs (all already produced):
1. Radar top-score distribution — median score of the top 8 candidates.
2. Ledger trend expectancy — with-trend vs against-trend grids per bot-hour
   (Phase A), rolling 7 days.
3. Volatility regime — ATR1h% tercile from the Phase-2 klines.

States and policy effect:
- `ACTIVE` — normal entry cadence.
- `SELECTIVE` — raise the entry score threshold one quartile; only
  with-trend entries.
- `WAIT` — no new entries; running bots keep harvesting grids untouched
  (this is the grid-yield-optimal action in chop).

Transitions use hysteresis (2 consecutive hourly evaluations) and the gate
is wired exactly like learned rules 16-18: advisory (log-only) → shadow
(would-have-gated counterfactual in the daily review) → binding. Dashboard
shows the state so "enter / change coin / exit / wait" is explicit system
state, not judgment. Promotion minimums: ≥14 days of ledger expectancy and
≥40 classified entry outcomes before the gate may bind; auto-revert to
advisory if rolling 7-day expectancy signal flips sign twice in 14 days.

## Phase D — nightly walk-forward autoresearch

Offline replay harness over the Phase-2 sqlite: replay candidate entry
thresholds and range-width choices on rolling windows, always against a
null arm (same layouts, entries allowed by a coin flip with the same base
rate). A candidate graduates to the proposals ledger only if it beats the
null arm by a pre-registered margin on ≥3 consecutive windows and never
loses to it by that margin on any window. No provider calls; runs from the
recorded history only. This is the "autoresearch" loop that turns the
ledger + sqlite into a steady stream of testable proposals.

## Phase E — learned exit and range thresholds

Same staged promotion for exit-side constants currently hand-fitted:
range-width multipliers per volatility tercile, ladder advance pacing, and
min-hold tuning per class (LOW_YIELD entries may warrant shorter holds;
RANGE_BREAK_EARLY entries warrant wider admission buffers). Exit learning
must never loosen the risk closes (STOP_LOSS, RANGE_BREAK, RISK_LIMIT stay
non-negotiable and exempt from min-hold).

## Execution order and status

| Phase | Status |
|---|---|
| A entry-outcome ledger | next (Codex, one PR) |
| B CoinGlass history | **implemented on this branch** |
| C regime gate | after A has ≥40 outcomes |
| D autoresearch replay | after A+B accumulate ~2 weeks |
| E exit thresholds | last; needs D's null-arm harness |

Sample reality check: at the current cadence (4-6 closes/day) the binding
minimums arrive in ~2-3 weeks. Until then everything runs advisory/shadow
by design — the system learns to wait before it learns to act.
