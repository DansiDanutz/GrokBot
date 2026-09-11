# Autopilot Phase A handoff

Phase A only: pure paper grid accounting in `trader/papergrid/`.
Base: merged default `f2b157f1ac2d71bf6b4af5088a5b351e9c8173aa` (PR #19).
Branch: `codex/autopilot-phase-a`. The final PR head identifies this evidence commit.

## Commits

- `0a4ee43`: first commit copies issue #16 comment 5636857052 verbatim into
  `docs/autopilot-spec.md` under “Phase A accounting”.
- `9a2ceb2`: immutable `open_bot`, `step`, `close_bot`, synthetic fixtures and tests.
- This documentation commit: handoff and fixture provenance.

## Verification

RED: the new test module failed with `ModuleNotFoundError: No module named
'trader.papergrid'` before the engine was present.
GREEN: 16 new engine tests; full gates pass with **38 Node + 291 paper Python +
129 trader Python = 458 tests**, plus a clean staged secrets gate.
Both gates ran before each commit. Existing SQLite ResourceWarnings remain in
the inherited radar tests; they are unrelated to this pure engine.

Coverage includes geometric lines, nearest empty line, actual-price seed sizing,
ordered multi-line fills at limit prices, seeded pair accounting, net-position
reversal, separate grid profit versus net, candle traversal, funding before fills
and stops, negative funding receipts, multiple missed funding boundaries,
JSON-roundtrip restart idempotency, risk-event emission, immutable inputs and
frozen close state. An in-memory funding-after-fills mutation failed the ordering
assertion (164.17 versus the required 300), confirming that test detects reversal.

Three committed fixtures contain 200 synthetic one-minute candles each for
oscillation, rising and falling prices. A separate synthetic tick series tests
repeatability. These are offline unit fixtures, not historical performance runs.

## Accounting and integration notes

`grid_profit` is gross adjacent-line income. Equity is `notional_usdt +
realized_pnl + unrealized_pnl - fees_paid - funding_paid`; grid profit is never
added again. Orders store line index, buy/sell side and paired-line index.
Only the adjacent order fills next, preserving one empty line while open.
Nearest-line ties select the lower index; equidistant candle extremes visit the
low first. Range-break streaks count whole updates at their final price; the
third consecutive out-of-range update emits the event. Stops emit without closing.

Funding overrides apply to the supplied update; the spec rate remains the default.
Phase B must supply current boundary rates. Duplicate/older updates are no-ops.
Close cancels orders, accounts for due funding and flattening fees, and counts no
additional grids. Events carry required identifiers and enum types plus numeric
fields; close reasons additionally use the zero-based `CLOSE_REASONS` code.

No Phase B/C implementation, runtime deployment, provider calls, installed-agent
changes, Telegram sends, real orders or archived research imports occurred.
The Phase B amendment and RAY assertion tolerance of **17–21 grids/h** remain
scheduled for Phase B's first specification commit. Await Claude's gate before
merging this PR or starting Phase B.
