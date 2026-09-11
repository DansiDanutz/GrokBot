# Grid KuCoin handoff for Claude

Status: **source research implemented; research acceptance partial; decision SHELVE.**
No deployment or live recommendation is justified. This is an unmerged audit
handoff, not a claim that KuCoin parity or the monthly performance gate passed.

Branch: `codex/grid-kucoin`, forked from PR #11 merge `3db5613`.
Tracking: [issue #13](https://github.com/DansiDanutz/GrokBot/issues/13).
Implementation head: `50e963f`. The final documentation commit is the frozen
PR head; its exact SHA and matching Ubuntu/macOS CI results are posted on the PR.
No merge or Phase 3 work is authorized by this handoff.

## Governing requirements and commits

[Consolidated requirements](../specs/grid-kucoin-dan.txt),
[Neutral and 5% stop amendments](../specs/grid-kucoin-addendum.md), and
[official-source semantics](../kucoin-futures-grid.md) govern this branch.
Latest stop rule: every new strategy direction closes at or before **5% beyond
either range edge**. Range analysis uses confirmed support/resistance; no
replacement candidate is needed to honor the stop. Ordinary range exits before
that barrier still invoke the hourly replacement policy.

| Task | Commit | Acceptance |
| --- | --- | --- |
| 5: preregistration, before any sweep | `2945fcd` | Registered primary/secondary metrics, 32 combinations, prior training and July/August holdouts. |
| Official semantics and golden fixtures | `c6e5ef8` | Sources cited, observations preserved, assumptions distinguished. |
| 1: three-mode replica and calibration | `e2f735d` | Pure model and accounting tested; exchange quantity/liquidation parity **fails**. |
| 2: direction, range, reserve, grid sizing | `62e179b` | Both nominal return and actual rounded fill profit floors tested; dependent on uncalibrated engine. |
| 3: radar, tracker, replacement | `09e66b6` | Scenario tests pass; Volatility-column validation remains **unknown**. |
| 4: snapshot, replay, baselines, decision gates | `69c6702` | Offline implementation tested; available market evidence is **insufficient**. |
| 6: operator CLI and scheduler example | `d939516` | Offline CLI, immutable artifacts and safety boundaries tested; example not installed. |
| Bound full-universe replay memory | `50e963f` | Stream one coin at a time; preserve coverage and candidate semantics. |
| Latest 5% stop/support-resistance amendment | `f5aabd3` | Both edges/all directions, inward tick rounding, adapter propagation and confirmed-pivot tests pass. Preregistration amended before rerunning. |

Each task was staged separately and both gates passed before its commit.
[Captured gate outputs](grid-kucoin-gates.md) include the last 20 output lines
per command. [Red-first records](grid-kucoin/red-first.md) preserve intentional
failures before implementation. Task 1 onward used a detached temporary
verification worktree with the **identical staged Git tree**, preventing
concurrent uncommitted tasks from changing the tested commit contents.
Final source gates: **38 Node + 279 existing paper + 172 grid = 489 tests**.
The final documentation gate also runs in the implementation checkout.

## Calibration against the real observations

The historical running Margin field is treated as total collateral, based on
RAY's confirmation and running screens. SOL therefore uses 7,000 plus 3,000
reserve, for 10,000 total; the four-form baseline totals 23,000. Both margin
interpretations remain in the separate retrospective diagnostics. Historical
margin amendments and original per-order quantities are still unknown.

| Long bot | Observed USDT/arbitrage | Fixed-quantity estimate | Dan margin formula | Estimated / observed liquidation | Profit ±15% | Liquidation ±10% |
| --- | ---: | ---: | ---: | --- | --- | --- |
| HEMI | 0.88958 | 1.05643 | 1.07414 | 0.007661 / 0.006928 | FAIL | FAIL |
| BTR | 0.88816 | 1.26813 | 1.29372 | 0.126881 / 0.100390 | FAIL | FAIL |
| MOVR | 1.49982 | 4.40531 | 4.61359 | 0.822059 / 0.495000 | FAIL | FAIL |
| SOL | 1.29217 | 1.50902 | 1.55506 | 74.892635 / 69.651000 | FAIL | PASS |

The requested margin formula does **not** explain all four historical averages
within tolerance. Do not tune quantities to those expected answers and call
that predictive validation. The source also reports a conditional inventory
reconstruction using independent unrealized-PnL/order-count fields; this does
not change the failed forward-calibration result.

RAY Neutral 5x, 70 grids, 1.1–2.0, 1,000 used plus 200 reserve:

| Check | Modeled / observed | Result |
| --- | --- | --- |
| Tick interval | 0.0128 / 0.0128 | PASS |
| Percentage preview | 2.6000%–5.2182% / 2.61%–5.21% | PASS, errors 0.0100 / 0.0082 percentage points |
| Inventory with independently observed 17 lots/order | 544 long, 629 short | PASS structure reconstruction |
| Open orders with observed quantity | 140, split 75 buy / 65 sell; 69 initial fills | PASS structure reconstruction |
| Automatic quantity | 22 / 17 RAY per order | FAIL |
| Forward liquidation | 0.677042 / 2.418160 versus 0.3238 / 3.1121 | FAIL both sides |
| Liquidation using observed quantity | 0.444369 / 2.643955 | FAIL both sides |
| Initial unrealized | 0 without opening spread / −2.39 observed | NOT reconstructed; exact opening fills absent |

Contract multiplier 1, lot size 1, tick 0.0001 and maintenance 0.015 came from
RAY's public ticker in the detached copy. That observation predates the RAY
fixture caption; it is not asserted to identify the exact later risk tier.
Observed 17-RAY orders imply modeled adjacent-cycle net profit 0.1770–0.1950
USDT, distinct from the margin-return approximation. This discrepancy remains
visible. It is not evidence that the real bot earns +1 USDT per arbitrage.

Under Dan's approximation, 50 grids pays about 1.019 USDT at price 1.58 but
only 0.78 at price 2.00. The whole-range nominal floor permits 44 grids before
additional lot, dual-leg allocation, reserve and liquidation checks. Setup
prints **both percentage and USDT**, verifies the actual rounded quantity too,
and the scanner uses that accepted interval. It cannot use 70 finer grids to
inflate the rate of a proposed +1-USDT setup.

Full machine-readable calibration and 23-symbol Volatility results:
[sweep.json](grid-kucoin/sweep.json). Volatility verification is **0/23**, with
23 unknowns: no synchronized independent daily extrema establish the app's
formula. The amplitude formula remains a labeled candidate statistic.

## Detached data and gap report

Only a copied database was opened with SQLite. The source DB and WAL were
cloned using filesystem copies; source size/inode/mtime/ctime signatures were
stable across the accepted copy attempt. SQLite integrity checking and WAL
checkpointing occurred **on the copy only**. The selected copy has no journal
sidecars and opens read-only with `immutable=1` and `query_only=ON`.

Copy: `/Users/davidai/Sandbox/grokbot/grid-kucoin-offline/20260911/attempt-2/market.sqlite3`.
SHA-256: `3e9ad565528747996e675f4b0fc07c7aef5c7e4f2802a672616136b4a1db90d1`.
Provenance/signatures and per-symbol counts: [data-counts.json](grid-kucoin/data-counts.json).

| Table | Rows | Symbols | UTC observation bounds |
| --- | ---: | ---: | --- |
| 1-minute candles | 11,567,789 | 522 | June 13 01:00 to September 11 08:19 |
| Tickers | 41,760 | 522 | September 11 01:38:36 to 08:20:31 |
| Top of book | 41,760 | 522 | September 11 01:38:34 to 08:24:17 |
| Funding | 3,751 | 522 | September 10 02:00 to September 11 08:00 |

There are **13,198,072 missing minute slots within each symbol's own candle
bounds**, summed across symbols. This excludes missing leading/trailing history
and does not establish full active-universe coverage. All 3,751 funding rows
have an unknown `period_ms`; zero rows prove an eight-hour settlement interval.
A current predicted rate is not substituted for those historical settlements.

## Replay results and written negative decision

The available common hourly window is **2026-09-11 02:00–08:00 UTC**. The
system and random baseline evaluate all 522 observed symbols each scan; no
eligible bot can be established under the required history and filters.
Known low turnover, wide spread or funding exclusions are separated from
missing data. A gap in required candles remains a coverage failure.

| Available-window mode | Bots instantiated | Valid grids/hour, grid profit, funding, net, drawdown, closest liquidation | Status |
| --- | ---: | --- | --- |
| System: two 1,000-USDT slots | 0 | unknown | Partial coverage; no eligible setup |
| Four observed Long forms unchanged | 0 | unknown | Original inventory unavailable with starting price outside a fixed range |
| Same four symbols, system setup | 0 | unknown | Missing seven-day history for eligible candidates |
| Random radar, identical rules | 0 | unknown | Same universe/history limits |

No trade was invented to fill the two slots. Empty ledgers are not profitable
runs or proof of zero liquidation risk. Per-coin/direction metrics are unknown
where no covered run exists. The static four-form baseline is a counterfactual
using later observed forms, not a historically available trading signal.
Full available-window reports: [available-window.json](grid-kucoin/available-window.json).

| Holdout | Prior 7-day training trials | Selected parameters | Valid monthly performance |
| --- | --- | --- | --- |
| July 2026 | 32 not run: quote/book coverage absent | none | unknown |
| August 2026 | 32 not run: quote/book coverage absent | none | unknown |

The 64 trial records are explicit **not-run coverage statuses**, not completed
backtests. No favorable parameters were selected. Each holdout lacks historical
execution observations; monthly net, drawdown, grids and liquidation counts
remain null. The preregistered result is **SHELVE**: failed Long/Neutral parity,
unverified Volatility, missing two-month coverage and unknown performance.

An initial coverage-only negative sweep preceded Dan's later 5% amendment;
the amended preregistration was committed at `f5aabd3` before the final rerun.
A combined available-window rerun and a later system rerun returned exit 143
before completion. The reader now streams one coin at a time to bound retained
history; separate bounded per-mode runs replaced interrupted results. No runtime process was inspected
or controlled in response. Final reported artifacts use the completed reruns.

## Operator usage and remaining limitations

The command is an offline research tool. Supply a detached snapshot and a
strict public-form JSON (`schema_version: 1`, at most two `bots`). Each bot
requires `bot_id`, `pair`, `direction`, `leverage`, `used_margin`,
`reserved_margin`, `entry`, `low`, `high`, `grids`, `stop_loss`, `start_ms`,
`expected_start_gph`; optional `stop_loss_high`, `trigger`, `quantity`,
`multiplier`, `lot_size`, `tick_size`. Total used plus reserve must be 1,000.
Carry the setup's tick size so both effective stops match the app-valid prices.
An empty bots list audits the radar without claiming existing account positions.

```sh
python3 -m trader.research.kucoin_cli operator \
  --snapshot /ABSOLUTE/DETACHED/COPY/market.sqlite3 \
  --asof latest \
  --running /ABSOLUTE/OFFLINE/INPUT/running.json \
  --output /ABSOLUTE/NEW/OFFLINE/OUTPUT
```

`latest` selects the copy's latest complete UTC hour. The operator must supply
new detached observations to advance it. This command does not copy the live
database. Hourly directories contain radar, full modeled fill ledgers, tracker,
history and keep/replace verdicts. Repeated identical output is idempotent;
conflicting output is rejected. Protected roots, credentials, symlinks and
hardlinks are rejected. The example scheduler remains uninstalled.

A real offline CLI run at 08:00 UTC observed 522 symbols, recommended zero
forms and retained `actionable: false`; see
[operator-example.json](grid-kucoin/operator-example.json).

Remaining material limits:

- Actual exchange allocation, risk tiers and initial spread remain uncalibrated.
- OHLC visits adverse extremes first; critical-event timestamps are model times.
  Conservative portfolio risk bounds address simultaneous-asset ordering, not
  missing real execution. Outside-range duration is minute-close sampled.
- Hourly replacement estimates use bid/ask and retain actual modeled close
  gross PnL, fees and cost separately. Intrabar stop/liquidation spreads are
  unobserved; such exits invalidate execution-cost coverage instead of passing
  a positive result on zero-spread assumptions.
- The chosen fixed eight-hour funding convention cannot validate contracts with
  one-/four-hour settlements or unknown intervals. Missing settlement evidence
  blocks a run.
- Real fills and account state cannot be reconstructed perfectly from screenshots
  and candles. Every ledger is explicitly modeled, not actual KuCoin history.
- App support for the two protective controls must be confirmed separately.
  No protection guarantees an exact fill or prevents liquidation across gaps.

No v1 runtime, Phase 2 checkout/acceptance workspace, installed LaunchAgent,
ZmartyChat-paper-grid, Telegram bot, OpenMausBot export or credential file was
modified. No Dan-only operator action, paid call, live order, merge or Phase 3
work was performed. Phase 2 continuation was left as it stood at task entry.

After the frozen-head CI report, remain idle for Claude's audit.
