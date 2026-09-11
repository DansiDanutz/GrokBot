# Chart reading and funding-aware grid research

This is an offline research model, separate from the installed bot and control
experiment. The new 48-hour evaluation remains unstarted. No completed amended
replay outcomes are asserted here; smoke runs and partial checkpoints do not
replace the registered complete windows and controls.

## Candle construction and reconciliation

`trader/features/timeframes.py` aggregates stored one-minute KuCoin candles into
5m, 15m, 1h, 4h and 1d UTC buckets. Only closed buckets are visible. Open and close
come from the first and last minute; high/low are extrema and volume is summed.
Missing minutes can use the last already-observed close for indicators, with
zero synthetic volume and explicit minute coverage. An absent causal seed stays
unknown. Synthetic observations never generate crossing counts or executions.

The detached copy contained 11,189,229 minute and 378,560 hourly rows across
522 contracts. Of 62,467 jointly comparable complete hours, 62,466 matched
exactly at zero price/volume tolerance. One AVAXUSDTM hour, 26 June 2026 at
14:00 UTC, had identical OHLC but volume 375,562 versus 375,479 contracts,
a difference of 83. It remains a mismatch; no source row was repaired.
There were 318,659 partial source hours and 25,063 absent source hours.
These counts overlap other missing-counterpart categories and must not be
summed as independent failures. Full counters and the differing row are in
[the reconciliation evidence](roadmap-evidence/grid-kucoin-v3-timeframes.json).
Incomplete source history prevents a claim of full hourly-history validation.

## Five-cell chart read

Every timeframe reports EMA20/50 and their five-bar slopes, confirmed swing
structure, ATR, range position, strength, direction and heuristic confidence.
Pivots need two closed bars on each side. Finite causal fills may contribute
estimated pivots and indicator values, with minute-coverage penalties. They
are not reclassified as observed prices. Confidence is not a probability.

The main variant requires 1d and 4h agreement; conflicting, weak or unavailable
reads yield Neutral. The alternative uses 4h alone. The 1h read supplies the
range, with disclosed trailing twenty-hour extrema if suitable pivots are
unavailable. Filled inputs keep the resulting levels labeled estimated.
Range evidence requires at least 95% underlying minute coverage. The 15m and
5m reads supply pullback entry conditions. Long avoids a fresh high; Short
avoids a fresh low. Neutral requires the central 20–80% of the range and no
fresh breakout. The last signal bucket must contain real observations.

An already-satisfied directional trigger is cleared from the executable paper
form and sizing uses the current tick-aligned entry reference. A genuinely
pending trigger reserves the bot's allocation and waits. Neither is permission
to place an exchange order.

The macro/SOL gate restricts new entries only. Its source list, evidence and
unknown-membership behavior are documented in
[the regime specification](kucoin-regime-metadata.md). The gate-off variant
retains the same readings and records what the gate would have allowed.

## Economic selection

The inherited consolidated-spec market filters remain 500,000 USDT daily quote
turnover, at most 0.1% spread, seven-day listing age, funding within ±0.1% per
eight hours, and top-of-book depth covering one grid on both sides. These
supersede the earlier draft's 5-million/0.05% thresholds. Explicit candle-only
history skips unavailable spread, depth and funding-window filters. Depth is
evaluated for each count during the sweep, before selecting the top three, and
checked again by radar. A larger-order count can fail while a smaller-order
count remains feasible; this preserves the same one-grid depth requirement.

For each admissible coin, search integer grid counts from 2 through the lower
of 200 and a known venue maximum. Quantities follow the existing unvalidated
allocation model, at 1,000 USDT used margin plus 200 reserve and 5x. The reserve
does not increase order notional. Quantity and liquidation estimates remain
labeled; calibration does not suppress a research offer.

Each completed cycle uses base quantity times price interval, less both 0.06%
fill fees and projected funding during that leg's empirical median hold.
Admission requires positive net throughout the range and a configurable safety
reserve, default 20% of both fees. Funding credits cannot rescue a failed fee
floor. Unknown funding or hold data leaves a provisional offer that cannot arm
a funded paper position. There is no fixed one-USDT minimum.

Count search uses observed close-path paired cycles over 24h and 7d. Gaps and
boundary touches censor pairs; later re-entry creates an estimation episode,
not a restart of a stopped portfolio bot. Rank by the smaller of the two
income/hour estimates. The radar and operator show the top three counts, net
USDT per grid, KuCoin percentage, GPH and income/hour. Seed closes are excluded
from completed-grid income.

## Funding and replacement evidence

The public history backfill retained 31,794 settlement records for 70 candidate
or reference contracts. Sixty completed API traversal; ten retained partial
pages before an invalid non-list response. This is not full-universe or
independently verified historical coverage. See
[the funding evidence](roadmap-evidence/grid-kucoin-v3-funding-history.json).
Requests used KuCoin's unauthenticated public endpoint, a shared token bucket
at one request/second, and per-page resumable sidecars outside the repository.
[KuCoin public funding-history documentation](https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history).

Forecasts use the latest causally available settled rate after an assumed
60-second publication lag. Cadence is inferred from settlement timestamps;
irregular or stale history disables a projection. A historical cadence change
can therefore conservatively suppress later forecasts. Actual replay cash uses
recorded settlement times, before same-time orders, never an assumed eight-hour
clock. A carried mark is labeled estimated. Funding is allocated separately to
each held slot, including opposite Neutral legs; seed funding stays separate.

Replacement compares six-hour funded grid income with an immutable starting
estimate. Underperformance means below half that starting income or fewer than
two completed grids/hour. A full six-hour window and at least 95% actual minute
execution coverage are needed; proven closed-flat time is covered. Unknown
income is not zero. An ordinary switch must recover closing losses and fees
plus the new opening budget over the comparison horizon. A higher-income coin
can qualify despite lower GPH. At most two 1,200-USDT allocations fit the
2,400-USDT bankroll; losses can leave insufficient cash for a replacement.

Either range edge still closes all paper inventory. There is no profit-target
stop, five-percent wait, or forced closure solely because the regime changes.
An observed gap can cause liquidation before a solvent stop; the model does
not guarantee that losses remain inside the range.

## Registered variants and limits

[The committed preregistration](../research/preregistration/grid-kucoin-v3-income-chart.json)
defines gate on/off × 4h-only/4h+1d bias. All outcomes are reported; the holdouts
do not select a winning variant. Three consolidated-spec controls are preserved:

| CLI mode | Preserved comparison | Variant reporting |
| --- | --- | --- |
| `four_observed_long_forms_unchanged` | Fixture-derived forms and capital with fresh modeled seed positions when the start is valid; original holdings remain unknown and no original stop is invented. | Independent of chart/regime variants: run once per window and reference that result from each variant report. |
| `same_four_symbols_system_setup` | The new rules restricted to those four symbols, with two 1,200-USDT slots and the same entry, funding, risk and replacement checks. | Run for each registered variant. |
| `random_radar_identical_rules` | Registered-seed random choices among eligible radar alternatives, preserving the same economic, funding, stop and capital rules. | Run for each registered variant. |

The unchanged control's fixture-derived capital is reported separately from the
2,400-USDT system bankroll. Grid count and fractional base quantity follow the
existing fixture interpretation of displayed order counts; this is not a
reconstruction of original holdings. A missing start price or a start outside
its fixed range is partial, never verified zero performance. If the start is
valid, the model seeds fresh initial inventory. The 15% drawdown denominator is
initial total portfolio collateral including reserves: 2,400 USDT in system
modes and the fixture-defined total for the unchanged control.

July and August were already inspected under earlier policies and are not
claimed to be pristine unseen data. A full result must include grid income,
total net after floating losses and costs, stops, drawdown and liquidations.
Missing coverage cannot authorize deployment. No paper timer starts from a
source commit, a backtest, or a dashboard notice.

## Reproduce an offline variant

Run from a clean, frozen checkout. Supply an explicitly attested detached SQLite
copy, the retained public funding JSON, and a new output directory. No API call
is performed by this command:

```sh
python3 -m trader.research.chart_replay_cli \
  --snapshot /absolute/detached/market.sqlite3 \
  --funding-history /absolute/detached/funding-history.json \
  --registration research/preregistration/grid-kucoin-v3-income-chart.json \
  --start 2026-07-01T00:00:00Z --end 2026-08-01T00:00:00Z \
  --bias-mode 1d+4h --regime-gate on --mode system \
  --chunk-hours 6 --max-chunks 4 \
  --output /absolute/new/offline-july-main
```

Repeat the identical command to continue from the last complete chunk. The
default invocation runs one six-hour chunk; the example permits four chunks.
Changing source, input hashes, membership registry, registration, window or
variant requires a separate output. A completed result is immutable and its
digest is checked on reread. `result.json.gz` retains the execution ledger and
stored decision evidence; scan diagnostics are compacted as described below.
`summary.json.gz` retains metrics, partial statuses and funding diagnostics and
is not a substitute for the execution ledger.

Use `4h-only` and `--regime-gate off` for the other registered variants, and
`--mode random_radar_identical_rules` for the matching random control.

For either four-symbol control, replace the example's `--mode system` with
its mode name, supply a separate output directory, and add the explicit fixture
option. For example, substitute these flags into the command above:

```sh
--mode four_observed_long_forms_unchanged \
--fixtures tests/fixtures/kucoin-grid-bots-20260911.json
```

The fixture JSON must contain exactly four forms with `symbol` fields, either
as a list or under `running_bots`. Its hash is part of the resume binding.
`--fixtures` is required for both four-symbol modes and rejected for `system`
and `random_radar_identical_rules`. The unchanged control still needs one
declared `--bias-mode`/`--regime-gate` combination for the CLI binding, even
though its original forms are variant-independent. Share the completed result
by reference; do not resume its output with different variant flags.

The operator command also accepts `--strategy income_chart_v3`, `--bias-mode`,
`--regime-gate`, and `--funding-history`. Its existing `--snapshot`, `--running`,
`--asof`, and `--output` arguments remain mandatory. `--candle-only` is explicit
research mode; otherwise quote/book filters remain. The running document accepts
only public paper form fields and optional available paper cash, never keys or
credentials. Missing funding produces labeled provisional offers. The bundled
Solana membership list is validated and passed through both command paths.

## Stored replay evidence

Compaction applies only to stored `income_chart_v3` scan views. Execution and
search consume the full in-memory candidate data, not these compact views.
The full model fill ledger, including initial seeds, opens, closes and recorded
funding events, remains available. Stored chosen forms retain their config,
quantities, stops, preview, entry/range evidence and economic scalars without
recalculating those values.

Selected radar rows retain their five chart cells and the top three count
tradeoffs. Repeated nested candidate trees are removed. Search summaries retain
count limits, evaluated counts, assumptions, rejection totals and reason
histograms, plus the first three rejected-count examples.

Every rejected coin still has a record with its reason and coverage. Its
`setup_summary` and `timeframe_reads` retain concise pre-entry evidence, while
`omitted_reproducible_fields` names discarded diagnostic trees. Full omitted
setup/chart diagnostics must be regenerated with the operator using the same
bound source, inputs, timestamp and parameters; they cannot be recovered just
by expanding stored references.

Repeated regime `raw_read` dictionaries are stored once per scan in
`regime_read_pool`, keyed by a SHA256 digest. A `raw_read: {read_ref: digest}`
entry expands to that scan's `regime_read_pool[digest]`. This recovers the exact
original raw regime evidence; macro/SOL decisions, source classifications,
allowed directions and reasons remain alongside it. The pool is local to each
scan, so references must not be resolved against another scan's pool.

Rejected chart histories can omit dense indicator placeholders after validation
proves the window invalid. The internal prepared record marks `dense_omitted`;
observed rows, coverage and rejection reason remain exact. It cannot seed dense
reuse. Valid histories and the default preparation API remain unchanged. This
optimization does not change the radar or replay decisions.

## Recorded operator check

The offline CLI was exercised at 2026-09-11 08:00 UTC against the attested
copy, with no running paper bots and 2,400 USDT assumed paper cash. It scanned
522 contracts, retained four modeled offers and recommended no funded entries.
DOGE and SOL lacked a qualifying entry; BTC and ETH had entry conditions but
failed the projected-income versus opening-fee budget. The scan therefore did
not fill two slots merely to keep them occupied.

[The operator excerpt](roadmap-evidence/grid-kucoin-v3-operator.json) records
five cells, top-three count scalars, range/form values, funding terms and gate
reasons. It includes the complete private report's hash and explicitly names
omitted diagnostic detail. These are historical model outputs, not live quotes
or instructions to place orders.
