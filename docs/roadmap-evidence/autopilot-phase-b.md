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
