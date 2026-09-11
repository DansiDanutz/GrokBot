# DansLabTrader: professional trader-bot roadmap

Prepared 2026-09-11 by Claude (audit: https://claude.ai/code/artifact/80844880-3c80-4614-80bc-58e58e646717).
Executor: Codex, working in this checkout (`~/ZCodeProject/GrokBot`). Reviewer: Claude re-audits each phase.
Owner and only person who may enable live trading or spend money: Dan.

## Ground rules (read before every task)

1. **Never touch the v1 evidence run.** Do not edit, restart, reseal or repoint
   `~/Sandbox/grokbot/zmarty-paper-runtime`, `com.danslab.zmarty-paper48`,
   `com.danslab.trader-publisher`, or the active checkout
   `~/ZCodeProject/ZmartyChat-paper-grid`. v1 keeps producing 48 h / daily / weekly
   audits as the control. Everything new runs in its own runtime and its own launchd label.
2. **No live orders, ever, from this roadmap.** Live mode is a separate Dan-only step (Phase 8).
   No exchange key with trade permission is created or stored by Codex.
3. **No secrets in the repo, in plists, or on the iCloud Desktop.** Secrets live in
   `~/.openclaw-secrets/*.env` (0600) or the dedicated trader user's environment.
   Print `[REDACTED]` for any credential-like value. Run `npm run verify:secrets` before every commit.
4. **TDD, small commits, conventional commits.** Red test first, then implementation,
   then `npm run verify`. One task per commit. Never `git clean -fdx`, never force-push.
5. **Do not modify** `~/.openclaw`, `~/.claude`, `~/.paperclip`, any Telegram bot,
   any LaunchAgent that is not created by this roadmap, or the OpenMausBot runtime export.
6. **No paid API calls or purchases** without Dan's explicit go for that specific task.
7. **Immutability and file size:** new objects, no in-place mutation of shared state;
   files under 800 lines; functions under 50 lines; no magic numbers (constants module).
8. **Evidence before "done".** Each phase ends with `docs/roadmap-evidence/phase-N.md`
   (see Handoff protocol). Claude re-audits from that file, not from chat claims.

## Target architecture

```
paper_grid/                  (existing engine, kept as accounting core)
trader/
  data/        klines, funding, OI, orderbook, universe registry  (SQLite)
  features/    indicators, regime, CoinGlass features
  strategies/  rebound_v2, mean_reversion_majors, funding_carry, breakout_trend, pump_fade_short
  risk/        position sizing, portfolio heat, halts, kill switch, pre-trade checklist
  execution/   order intents -> paper fills (now) / exchange adapter (Phase 7, shadow only)
  research/    walk-forward replay, metrics, null arms, sweeps, preregistration
  ops/         runtime v2 controller, websocket quotes, heartbeat, Telegram alerts, journal
config/        strategy and risk YAML/JSON, launchd templates
docs/roadmap-evidence/       one file per phase
```

Runtime for v2: `~/Sandbox/grokbot/trader-v2-runtime`. Market data: `~/Sandbox/grokbot/market-data/`.
Ports: v2 dashboard 127.0.0.1:8874. Labels: `com.danslab.trader-v2`, `com.danslab.market-data`.

## Phase 0: Ground and stabilise (Day 1)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 0.1 | Commit the in-progress work (analytics, telemetry_metrics, replay, REPLAY.md, engine/experiment edits). Update `config/paper-source.lock.json` to state that this checkout is now the development source and v1 is frozen at 6ee309c2. | `paper_grid/*`, `config/paper-source.lock.json`, `docs/paper-operations.md` | `npm run verify` and `npm run verify:secrets` green; git clean. |
| 0.2 | Fix H6: write `/opt/homebrew/bin/node` (stable symlink) instead of `process.execPath` into the MCP server command; doctor checks the stored command exists. Existing config.json must not be rewritten automatically; add `npm run doctor` warning with the one-line fix. | `scripts/runtime.mjs:27`, `scripts/doctor.mjs`, `tests/runtime.test.mjs` | Test proves new installs get the stable path; doctor flags the versioned path. |
| 0.3 | Fix M7: CoinGlass key path becomes config (`PAPER_GRID_SECRETS_FILE`, default `~/.openclaw-secrets/paper-grid.env`). Never a Desktop path. v1 keeps its own path untouched. | `paper_grid/coinglass.py:19`, `paper_grid/test_coinglass.py`, `docs/paper-operations.md` | Test: Desktop path rejected with a clear error; env override honoured. Dan copies the key once. |
| 0.4 | Fix M8/M9/L7/L9: delete stale `.runtime/*.tar` after successful extract; anchor tar exclude to `^enterprise/`; write a pidfile on server spawn and use it in `start.mjs` and `doctor.mjs`; log server stdio to `.runtime/logs/` with size-based rotation; fleet-mcp logs probe errors to stderr. | `scripts/setup.mjs`, `scripts/start.mjs`, `scripts/lifecycle.mjs`, `scripts/fleet-mcp.mjs`, tests | Tests for each; `.runtime` shrinks by 65 MB. |
| 0.5 | Fix L4/L5: nonce-based CSP for both dashboards; `chmod 700` publisher `auth/`; delete `vercel-publisher/initial-site/`. | `paper_grid/server.py:80`, `paper_grid/publish_vercel.py:22`, docs | Headers verified with curl in evidence file. |
| 0.6 | Propose (do not execute) retirement of `com.zmarty.ladder-paper-trader` and the token rotation for C1: write `docs/roadmap-evidence/phase-0-dan-actions.md` with exact commands for Dan. | docs only | Dan executes; Codex never edits those plists. |

## Phase 1: Instrumentation (Day 1 to 2)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 1.1 | Fix H2: `_buy` and the candidate loop emit `buy_rejected` events with reason (`lots_lt_1`, `thin_ask_depth`, `insufficient_cash`, `stop_inside_range`, `expected_net_lt_target`, `score_below_min`) and the numbers that failed, per arm, per tick. | `paper_grid/engine.py:175-202, 339`, `paper_grid/test_engine.py` | Every `False` return has a matching event in a test; audits count them. |
| 1.2 | Fix M4: persist per-arm equity per observation; audits compute windowed drawdown from 5-minute equity. | `paper_grid/experiment.py`, `paper_grid/audits.py:180-189`, tests | Drawdown test with an intra-window dip that 30-minute marks would miss. |
| 1.3 | Fix L1: error records carry a bounded traceback (last 20 frames) and the tick timestamp. | `paper_grid/experiment.py:338` | Test asserts traceback presence and size bound. |
| 1.4 | Fix M5: audits call `coinglass.apply_filter`; delete the hard-coded 1800/7200/3/0.60; add an equivalence test. | `paper_grid/audits.py:216, 232-236`, `paper_grid/test_audits.py` | One source of truth for the filter. |
| 1.5 | Report metrics: profit factor, exposure (time in market), average hold, per-symbol PnL, MAE/MFE per trade, and "baseline PnL of trades the filter blocked". | `paper_grid/audits.py`, `paper_grid/analytics.py`, dashboards | Sample report in evidence file shows all fields with a synthetic ledger. |
| 1.6 | Fix M3: one documented day convention. Halt day and daily audit both use the Bucharest 00:00 boundary; archives stay UTC but reports disclose it. | `paper_grid/engine.py:53`, `paper_grid/audits.py:62-70`, `CONTINUOUS.md` | Test for a halt at 08:59 and 09:01 landing in the right daily window. |

Evidence: `docs/roadmap-evidence/phase-1.md` with test counts and a rendered sample audit.

## Phase 2: Data layer (Day 2 to 4)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 2.1 | `trader/data/store.py`: SQLite schema for klines (1m, 1h), funding, open interest, top-of-book snapshots, CoinGlass hourly liquidations; WAL mode; idempotent upserts. | `trader/data/store.py`, `trader/data/schema.sql`, tests | Schema migration test; upsert twice yields one row. |
| 2.2 | `trader/data/kucoin_backfill.py`: 90-day 1m and 1h backfill for the tradable USDT-perp universe via public klines with a token-bucket rate limiter and resumable checkpoints. | `trader/data/kucoin_backfill.py`, tests with recorded fixtures | Gap report shows zero gaps for BTC, ETH, SOL; run time and row counts logged. |
| 2.3 | `trader/data/updater.py` + LaunchAgent `com.danslab.market-data` every 5 minutes: incremental klines, funding, OI, full ticker snapshot, top-of-book for the active universe. | `trader/data/updater.py`, `config/launchd/com.danslab.market-data.plist.example` | 24 h of uninterrupted updates; lock prevents overlap. |
| 2.4 | `trader/data/universe.py`: registry with first-candle date (listing age), 30-day turnover, ATR statistics, contract multiplier and lot size. | `trader/data/universe.py`, tests | Registry lists ≥ 100 contracts with listing age. |
| 2.5 | Data-quality checks: duplicate candles, non-monotonic timestamps, zero-volume runs, funding gaps; results into a `data_quality` table and `/api/data` on the v2 dashboard later. | `trader/data/quality.py`, tests | Quality report in evidence file. |

## Phase 3: Research harness (Day 4 to 7)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 3.1 | Extend `paper_grid/replay.py` into `trader/research/replay.py`: build synthetic observations from the SQLite store and drive `engine.step` deterministically; walk-forward splits (train n days, test m days, roll). | `trader/research/replay.py`, tests | Replaying v1 config over the v1 observation window reproduces v1's ledger within 0.01 USDT. |
| 3.2 | `trader/research/metrics.py`: expectancy, profit factor, Sharpe and Sortino (per trade and per day), max drawdown, exposure, average hold, MAE/MFE, bootstrap 95 % CI on expectancy. | `trader/research/metrics.py`, tests | Golden tests on a hand-computed trade list. |
| 3.3 | Null arms: random-entry with identical exits; symbol-shuffled entries. Every sweep reports strategy versus both nulls. | `trader/research/null_arms.py` | Null arm expectancy near zero after costs on a real month. |
| 3.4 | Pre-registration: `research/preregistration/<id>.json` (hypothesis, parameters, decision rule, author, date) must be committed before a sweep runs; the sweep CLI refuses an unregistered id and writes results to `research/results/<id>/`. | `trader/research/sweep.py` | CLI refuses unregistered run; results are reproducible from the id. |
| 3.5 | Fix H1 in the v2 engine: rotation uses the score frozen at entry or a PnL-and-age rule; never rotates out a position with net > 0. Config flag keeps v1 behaviour for replay parity. | `paper_grid/engine.py:347`, tests with dynamic scores | Dynamic-score rotation test passes; v1 parity test still passes with the flag off. |
| 3.6 | ATR-scaled brackets and risk-based sizing as engine options: target ≥ 1 ATR, stop ≤ 1.5 ATR, size = risk budget / stop distance, respecting lot and multiplier. | `paper_grid/engine.py`, `trader/risk/sizing.py`, tests | Sizing property test: risk never exceeds budget at the stop. |
| 3.7 | Fix H5: intra-tick stop model. Stops fill at min(bid, 1-minute low since last tick); targets at max(ask, 1-minute high) only if the stop was not hit first. | `paper_grid/engine.py:273, 314-319`, `market.py:46` | Test with a wick that hits both: stop wins. |
| 3.8 | Run the first registered sweep on rebound_v1 and rebound_v2 over the backfilled 90 days and write the result honestly, including "shelve" if negative. | `research/results/` | Result committed with CI and null comparison. |

## Phase 4: Strategy book (Day 7 to 12)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 4.1 | Strategy interface: `signal(features, positions, config) -> list[Intent]` with pure functions and typed dataclasses; engine consumes intents. | `trader/strategies/base.py`, tests | Contract test every strategy must pass. |
| 4.2 | Shorts in the engine: sign-aware net, price-drop stop, funding paid or received, liquidation-distance check. | `paper_grid/engine.py`, tests | Mirror tests of every long test for the short side. |
| 4.3 | `mean_reversion_majors`: BTC, ETH, SOL, z-score of close versus 20-period mean on 15 m, entry at abs(z) ≥ 2, exit at z = 0 or 1.5 ATR stop, reward-to-risk ≥ 1.5. | `trader/strategies/mean_reversion_majors.py` | Registered sweep result. |
| 4.4 | `funding_carry`: hold the side that receives funding when abs(rate) ≥ 0.05 % per 8 h and the 24 h trend agrees; paper models the hedge cost explicitly. | `trader/strategies/funding_carry.py` | Registered sweep result. |
| 4.5 | `breakout_trend`: 4 h range breakout with BTC regime filter (4 h EMA slope and breadth), ATR trailing stop. | `trader/strategies/breakout_trend.py`, `trader/features/regime.py` | Registered sweep result. |
| 4.6 | `pump_fade_short`: after a ≥ 40 % 24 h move and a CoinGlass short-liquidation burst, short on the first lower high, stop above the high, target 1 ATR. | `trader/strategies/pump_fade_short.py` | Registered sweep result. |
| 4.7 | Universe filters for long strategies: listing age ≥ 7 days, 30-day median turnover ≥ 5 M USDT, hourly ATR ≤ 4 %; ranking by liquidity-adjusted range. | `trader/data/universe.py`, `paper_grid/market.py:156` | Test that tonight's top five would have been excluded. |
| 4.8 | Fix M1: CoinGlass alignment. Burst at hour h−1, confirmation candle at hour h, independent of cache timing; the ≥ 25-hour rule becomes "≥ 25 hours or listing-age aware baseline". | `paper_grid/coinglass.py:134-138`, `paper_grid/market.py:188` | Alignment test with a synthetic burst hour. |

Shelve any strategy whose registered rule fails on two disjoint months. Write the negative result.

## Phase 5: Risk layer (Day 12 to 14)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 5.1 | `trader/risk/portfolio.py`: risk per trade 0.25 to 0.5 % of equity, portfolio heat cap 2 %, correlation cap (no more than two positions with 30-day return correlation > 0.7), daily −2 % and weekly −5 % halts, leverage ≤ 3x, liquidation distance ≥ 3 ATR. | `trader/risk/portfolio.py`, tests | Property test: no random intent sequence breaches a cap. |
| 5.2 | Kill switch: presence of `~/Sandbox/grokbot/KILL` flattens all paper positions at next tick and blocks new risk until removed; also a `trader kill` CLI. | `trader/ops/kill_switch.py`, tests | Integration test through the controller. |
| 5.3 | Pre-trade checklist function run on every intent: universe filters, spread, depth, quote age, risk caps, halts, kill switch; every rejection is an event (extends 1.1). | `trader/risk/checklist.py` | Exhaustive table-driven test. |

## Phase 6: v2 paper deployment and monitoring (Day 14 to 16)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 6.1 | v2 controller: multi-strategy book on `~/Sandbox/grokbot/trader-v2-runtime`, 1,000 USDT paper, LaunchAgent `com.danslab.trader-v2` on 127.0.0.1:8874, same atomic-state and lock guarantees as v1. | `trader/ops/controller.py`, `trader/ops/server.py`, `config/launchd/com.danslab.trader-v2.plist.example` | Restart test preserves state; single-writer lock proven. |
| 6.2 | WebSocket quotes from KuCoin futures public stream with polling fallback and staleness detection. | `trader/ops/quotes_ws.py`, tests with recorded frames | Quote age stays under 5 s for 24 h; fallback exercised in test. |
| 6.3 | Telegram alerts through the existing secrets env (never a plist literal): dead-man (no tick for 15 min), halts, kill switch, reconciliation mismatch, publisher stale > 45 min. Send to Dan's chat only. | `trader/ops/alerts.py`, tests with a fake transport | Each alert type fired once in a test and once for real with Dan's go. |
| 6.4 | Trade journal: every open, add, close with entry reason, exit reason, MAE, MFE, slippage, strategy id; exported nightly to `journal/YYYY-MM-DD.jsonl`. | `trader/ops/journal.py` | Journal row per event in test; nightly file present in evidence. |
| 6.5 | Public page: add a v2 section; publisher exports both v1 and v2 DTOs through the same allowlist discipline; Grok routine instruction v2 (review-only) in `config/paper-review-instructions-v2.md`. | `paper_grid/public_snapshot.py`, `paper_grid/public/index.html`, docs | DTO allowlist tests extended; no new fields without a test. |

Acceptance for the phase: seven days uninterrupted v2 paper operation with zero unexplained gaps.

## Phase 7: Execution layer, shadow only (Day 16 to 22)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| 7.1 | Exchange adapter interface and KuCoin futures implementation: place, cancel, query, positions, balances; idempotent `clientOid`; rate-limit budget; typed errors. Mode `shadow` signs requests against a mock transport; mode `readonly` uses a real key with **no trade permission** (Dan creates it) for positions and balances only. | `trader/execution/adapter.py`, `trader/execution/kucoin.py`, tests | Contract tests; readonly smoke shows balances with the key value redacted. |
| 7.2 | Order state machine: new, acked, partial, filled, cancelled, rejected; partial-fill accounting; reconciliation every 60 s against the adapter; any mismatch halts and alerts. | `trader/execution/orders.py`, `trader/execution/reconcile.py` | State-machine tests cover every transition; mismatch test halts. |
| 7.3 | Dedicated macOS user `trader` for the v2 process (Dan creates the user). Keys and Telegram token in that user's `~/.trader/secrets.env` (0600). LaunchAgent moves to that user's session. Agents in Dan's account cannot read it. | `docs/roadmap-evidence/phase-7-dan-actions.md`, launchd templates | Evidence shows `ls -l` permissions and a failed read from Dan's account. |
| 7.4 | Seven days of shadow: v2 paper fills versus what the adapter would have sent; slippage model recalibrated from shadow quotes. | `research/results/shadow-week-1/` | Zero reconciliation mismatches; slippage table committed. |

## Phase 8: Live ramp (Dan-gated, not part of Codex's scope)

| Tier | Capital | Gate |
|---|---|---|
| Rule file | none | `research/preregistration/live-ramp.json` committed: ≥ 100 closed v2 paper trades, profit factor > 1.3, max drawdown < 5 %, two separate weeks positive, shadow mismatches = 0, alerts proven. |
| 1 | 100 USDT, leverage ≤ 2x, one strategy | Rule met; Dan flips `mode: live` himself; trade key created by Dan with IP allowlist and no withdrawal permission. |
| 2 | 500 USDT | Four weeks at tier 1 meeting the rule again. |
| 3 | 2,000 USDT | Four weeks at tier 2 meeting the rule again. |

Weekly post-mortem: Grok routine summarises the journal; Dan decides changes once a week; nothing is tuned mid-week.

## Handoff protocol (Codex to Claude)

After each phase, Codex writes `docs/roadmap-evidence/phase-N.md` containing:

1. Task table with commit hash per task and "done / partial / skipped" plus the reason.
2. Exact commands run and the last 20 lines of their output: `npm run verify`, `npm run verify:secrets`, any data or replay runs.
3. Test counts before and after the phase.
4. Anything not done, and any deviation from this roadmap with the reason.
5. Live evidence where relevant: curl outputs, launchctl status, row counts, rendered report paths.

Then Dan tells Claude "audit phase N". From Phase 1 onward, Claude audits the
fixed PR head in a separate worktree before merge. Codex stays idle and does not
commit, merge or begin the next phase until the gate decision. Any changed PR
head invalidates the previous audit target. Claude updates the audit artifact
and fleet dashboard and marks findings fixed only with proof. See
[grokbot-roadmap-audit-protocol.md](grokbot-roadmap-audit-protocol.md).
This supersedes the original merged-result wording following the Phase 0 audit.

## Paste-ready Codex prompt

```
Read ~/ZCodeProject/GrokBot/docs/pro-trader-roadmap.md and ~/ZCodeProject/GrokBot/AGENTS.md.
Execute Phase 0 then Phase 1 exactly as written, one task per commit, TDD first,
npm run verify and npm run verify:secrets green before each commit. Never touch the v1
runtime, its LaunchAgents, the ZmartyChat-paper-grid checkout, Telegram bots, or any
existing plist. No live orders, no paid API calls, no secrets in files. When a phase is
complete, write docs/roadmap-evidence/phase-N.md per the Handoff protocol and stop for
Claude's audit before starting the next phase.
```

_Last verified: 2026-09-11_
