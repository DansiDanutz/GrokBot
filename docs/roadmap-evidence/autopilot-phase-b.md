# Autopilot Phase B handoff

Phase B only, branch `codex/autopilot-phase-b`.
Base: `ccfdab3c9ed2b9df43cf6d407aa41ed23fa788fa`, the default-branch merge of
PR #22 after Claude's PASS on `693566c` (issue #16, comment 5637169032).
The final PR head identifies this evidence commit. Await Claude's gate before
merging or beginning Phase C.

## Commits

- `733c1a1`: first commit amends the specification with direction slots, profiles,
  shared k(step), RAY tolerance and per-direction reporting.
- `0c195de`: records the three cap clarifications beside the constants, following
  issue #16 comment 5637218627, including the requested regression test contract.
- `339dc49`: daemon, pure policy, storage, public data adapter, opt-in Telegram,
  shared radar rate function, fixtures and tests.
- This documentation commit records the handoff.

## Verification

Both gates passed before every commit: `npm run verify` and
`npm run verify:secrets`. Full suite: **38 Node + 291 paper Python + 177 trader
Python = 506 tests**. The new Phase B test modules contain **46 tests**.
Existing SQLite ResourceWarnings in inherited radar tests remain non-failing.

New-module tests were exercised before implementation and failed on missing
modules/interfaces; implementation then passed the focused suite and full gates.
Additional review regressions cover response timestamps, protected path traversal,
bounded Telegram delivery and safe closures when the radar is unavailable.
All verification uses synthetic inputs, temporary databases/files and injected
clients. No real provider request, daemon deployment or Telegram send occurred.

Coverage includes balanced 2/2/2 selection, borrowed slots and four-per-direction
cap, freed SHORT refill, cooldown, every close reason, majors across profiles,
trend Movers capped while Neutral Movers remain eligible, reserve accounting,
RAY k(step) within 17–21 grids/hour, tick fills, per-boundary funding rates,
restart idempotency, snapshot atomicity, lock exclusion, signal handling,
monotonic scheduling, event bounds/rotation/retention, interrupted-write retries,
health alerts/recovery, daily directional summaries and delivery deduplication.

## Delivered behavior

`python3 -m trader.autopilot run` uses one loop with a ten-second public ticker
pass and five-minute decisions, plus radar mtime reloads. `once` is available for
an explicit manual pass. State and snapshots use atomic replacement; an owned
lock excludes a second instance. SIGTERM stops the loop cleanly.

The pure policy wraps the unchanged Phase A engine. Neutral bots reserve 200 USDT
without deploying it; the portfolio remains a 10,000 USDT starting ledger.
Snapshots provide directional totals, per-bot grids/hour and PnL samples, health,
last 20 closed rows and at most 2,000 portfolio samples. Funding uses historical
DB rates at each boundary, before fills; grid profit is never added to net again.

Public allTickers parsing normalizes KuCoin timestamps and admits only required
symbols into runtime state. Recovery ingests up to six hours of missing one-minute
candles for open symbols, then processes available whole unseen candles in order.
Events use numeric fields and a fixed type enum, with daily rotation, 30-day
retention and persisted event IDs for retry deduplication.

Telegram remains explicit opt-in through the existing credential wrapper's
environment variable. At most one message is attempted per pass, health first;
unsent lifecycle notifications remain queued. No token is persisted. Daily
08:00 Europe/Bucharest summaries compare LONG, SHORT and NEUTRAL separately.

## Limits and deferred work

- Real feed latency, exchange availability and installed daemon operation were
  not tested. The public last-trade price is the paper mark proxy.
- The partial minute containing the last processed tick is skipped during
  recovery: its exact remaining intraminute path cannot be reconstructed. Missing
  candles outside the six-hour ingestion window stay missing. This is not a
  tick-perfect exchange reconstruction.
- Funding falls back to the bot's configured rate when no prior DB rate exists.
- State reads are capped at 64 MiB; event endpoint preparation fails closed on a
  daily file exceeding 2 MiB. These operational limits are documented in the
  module README rather than represented as unlimited retention capacity.
- Phase C owns the public DTO, endpoints, paper page, publisher, RUNBOOK and
  uninstalled KeepAlive LaunchAgent example. None is deployed by this phase.

No protected runtime, installed launchd agent, credentials, archived research,
exchange orders or production publisher was changed. Operator instructions are
in `trader/autopilot/README.md`; they were not executed against a live database.

## Watchlist amendment — supersedes the original six-bot handoff

Specification commits: `c36668c` (watchlist), `3bc8747` (Phase C landing/cutover
requirements only), `4650660` (answers from issue #16 comment 5637441708).
Implementation: `2329d817e6eb3c6a86ab0d235468c5278d5bb5f2`.
This evidence update is the final commit identified by the new frozen PR head.

The spread rule is five points through 0.05%, linear to zero at 0.15%; turnover
is zero through 3M, linear to ten at 30M USDT. LIQUIDITY_TURNOVER and
LIQUIDITY_SPREAD preserve separate measurements. The shared scorer covers all
specified components and penalties with finite numeric values and clamps the
final score to 0–100. Existing radar rank_score and display ordering are preserved.

State and snapshots now carry core/bench (five entries each), the last 48 watchlist
events and scan metadata. Core admission uses the full qualifying radar universe,
not the display's truncated sections. Scores/directions refresh once per scan;
ten-point/two-hour promotion hysteresis applies. No more than one promotion or
replacement happens after cold start in any scan. All twice-missing core entries
drop immediately; if several drop, remaining seats refill one per later scan.
This preserves both mandatory removal and the explicit one-swap cap. Each
post-start promotion counts once in daily swap totals.

Only core coins can open bots. MAX_BOTS is now **five**, with preferred 2/2/2 slots
and borrowing capped at four of one direction. A demotion does not close an open
bot. Existing close rules and Phase A accounting remain unchanged. Older numeric
scan IDs cannot trigger stale labels, openings or miss counters; safety/age closes
still work when radar input is unavailable or regressed.

Telegram queues numeric per-scan payloads for changed watchlists, including the
original swap reason, and retries oldest first across restart/new scans. Unchanged
scans are silent. Expired payloads are removed before sending; the pending queue
is bounded to 720 entries / 30 days. The daily summary reports local-day swaps.
Watchlist log records use PROMOTE/DEMOTE/DROP/DIRECTION_CHANGE with validated
replacement symbols; all score fields remain numeric. No free-text explanations
are stored. Phase C owns sentence templates and public rendering.

Verification: **539 tests = 38 Node + 291 paper Python + 210 trader Python**;
`npm run verify:secrets` clean. Both gates ran before each amendment commit.
The amendment adds **33 tests**: five scoring, eleven pure watchlist transitions,
six admission/event integrations, six Telegram scenarios and five runtime cases.
Pre-existing policy fixtures/assertions were updated for five-bot admission and
complete scoring measurements; an outside-core replacement is now rejected.

RED evidence: missing scoring/watchlist modules, missing watchlist snapshot/event
support, admission outside the truncated sections, forced multiple promotions,
regressed-radar label closure, lost older notification on a later scan, missing
persisted pending scans, and expired notification delivery were observed failing
before their fixes. GREEN includes the full suite, plus independent bounded review
and correction of the cap, regressed-scan and notification retry/expiry issues.
Restart tests prove watchlist/event persistence without repeating swaps; old
Phase B state initializes watchlist fields without closing existing bots.

No live provider call, real order, Telegram send, daemon run, installed-agent edit,
protected-runtime change, replay/backtest or Phase C implementation occurred.
Real-feed/deployment behavior remains unverified. PR #23 stays unmerged; await
Claude's gate on the new frozen head before proceeding to Phase C.
