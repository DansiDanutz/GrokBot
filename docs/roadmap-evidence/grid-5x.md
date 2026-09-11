> Superseded before PR publication by Dan's consolidated grid-kucoin specification.
> This preserves the earlier source experiment; it is not the active handoff.

# Grid 5x source handoff — historical acceptance blocked

Issue: [#13](https://github.com/DansiDanutz/GrokBot/issues/13).
Branch: `codex/grid-5x`, created from the exact PR #11 merge `3db5613`.
Worktree: `/Users/davidai/ZCodeProject/GrokBot-grid-5x`.
The final unmerged PR head and CI are the audit target. No merge or Phase 3 work
is authorized. This report follows the roadmap handoff protocol, but does not
claim completion of the market-data acceptance requirements.

## Result and decision

**Source implemented; real sweep and current operator form not produced.**
No detached offline Phase 2 database copy was supplied. The acceptance workspace
was not inspected or copied. The written decision is **shelve — insufficient
evidence**, not a finding that the strategy has failed on actual market data.
Actual metrics are `null`, not invented zero returns.

- [Recorded decision](../../research/results/grid-5x.json)
- [Synthetic accounting example](../../research/results/grid-5x-synthetic.json)
- [Product semantics and citations](../kucoin-futures-grid.md)
- [Preregistration](../../research/preregistration/grid-5x.json)
- [Actual gate transcript tails](grid-5x-gates.md)

The neutral initialization and liquidation model are explicitly approximate.
KuCoin's published guides do not specify the exact neutral allocation algorithm.
The engine's exact paper rule is documented, rather than presented as proven
exchange equivalence. Full parity and historical evidence remain prerequisites
for a separate paper-deployment review.

## Task and commit evidence

| Task | Commit | Status | Evidence / limitation |
| --- | --- | --- | --- |
| Guides first | `cf89c74` | Done | Official KuCoin support citations before implementation |
| 6. Preregister | `a1add0e` | Done | Two disjoint monthly holdouts; grid rate primary; prior-training-only selection; written shelve rule |
| 1. Paper replica | `cf15cc0` | Partial | Source and tests done; neutral allocation and liquidation tiers are model assumptions |
| 2. Sizing | `26baf6e` | Done | Lot/tick rounding and worst-pair fee floor; 20 grids, 5x, 1,000 USDT approximately 0.526% step |
| 3. Scanner | `9c7f033` | Partial | Detached read-only copies; causal filters and rate ranking tested; no real snapshot ranking |
| 4. Replacement | `886b036` | Done, source | Configurable N/margin and switch-cost payback; no profit-target stop |
| 5. Walk-forward | `7be3298` | Partial | Replay, null, causal parameter selection and reports tested; real monthly sweep not run |
| 7. Operator output | `d82e3fb` | Partial | Form and replacement JSON tested; current best coin/form unavailable without snapshot |
| 1. Accounting corrections | `9bfa655` | Done | Equity extrema and funding on boundary inventory, tested red-first |
| 5. Replay corrections | `e9f9517` | Done | Executable post-close sizing, full range-exit price path, intrasegment drawdown and state export |

The documentation/preregistration commits contain no executable behavior.
Executable tasks began with failing tests. Baseline was **38 Node + 279 Python =
317** tests; final source suite is **38 Node + 279 paper + 82 grid = 399**.
Existing paper code and its golden fixtures are unchanged from `3db5613`.
No dependencies were added. Tests run on temporary synthetic data only.

## Conventions that affect interpretation

- A completed grid is a matched nonseed entry/exit pair. Seed liquidation profits
  are separate; a raw price crossing never increments completed-grid counts.
- Grid profit is reported both gross and net of its allocated two fill fees.
  Total net adds gross grid/seed/close PnL and signed funding, subtracts total fees
  once, and includes ending floating PnL. Grid-net profit is not added twice.
- Scanner ranks `min(crossings_4h/4, crossings_24h/24)`, using completed minute
  closes and a persistent step anchor. This is an activity proxy, not a guarantee
  of completed fills. Full 24-hour history is required; gaps are unknown evidence.
- Specified filters: spread at most 0.05%, turnover at least 5 million USDT,
  both top-of-book sides at least one grid after contract conversion, listing
  age at least seven days, and funding within ±0.05% per eight hours. Contract
  eligibility and missing/stale data are validated separately. No volatility,
  profit-factor, trend or discretionary stop-loss filter was added.
- Known funding periods are normalized for screening, with raw period/rate retained.
  Replay currently requires actual eight-hour settlement rows and valid mark quotes.
  A known four-hour coin may pass screening but cannot obtain complete replay
  coverage through its next eight-hour boundary. No absent rate becomes zero.
- The scanner runs every five minutes over one-minute histories. Range exits are
  checked each minute. When a candle exits the range, orders are canceled and
  inventory remains marked through that candle's end, where replacement occurs.
  This can expose additional intrabar loss/liquidation before the modeled close.
- Each candle is replayed along both open-low-high-close and open-high-low-close;
  the lower ending-equity path is retained. This is a path sensitivity convention,
  not observed sequencing. Critical equity marks at every fill feed drawdown.
  Spread is carried from a fresh prior quote; actual queue/partial-fill behavior
  and historical maintenance tiers remain unverified.
- Price fills at a settlement timestamp precede funding. The caller splits at
  every eight-hour boundary and uses the settlement mark. Earlier liquidation
  prevents later funding; funding itself can trigger liquidation.
- N-hour realized rate uses actual elapsed bot age until N hours are available.
  Replacement requires incremental rate strictly above the configured margin
  plus switch cost divided by the four-hour payback horizon and one-USDT target.
  The hurdle includes floating loss, close/open spread and both fees; positive
  floating profit cannot subsidize churn. Costs enter the ledger only through fills.
- Candidates are sized using executable capital after a hypothetical close.
  A real switch uses the same allocation, capped at the original investment;
  losses reduce available margin. No external top-up or borrowed fee reserve.
- A range exit may close into cash if no eligible replacement exists. Unknown
  data terminates an invalid replay segment without inventing a market close.
  Forced liquidation also ends that trial; these are not profit-target exits.
- Random null samples the entire eligible pool using seed `20260911`, with the
  identical grid, cost and replacement rules. It is not restricted to the top ten.
- Parameters are selected on the preceding seven days, maximizing completed grids
  per hour, then net on ties. Each July/August holdout retains its selected settings.
- Per-coin rates use bot-active time; overall rates use the full evaluation window.
  Per-coin and overall reports include realized grid profits, fees, funding,
  switch-realized floating PnL, drawdown, maximum floating loss, switch frequency,
  range exits and liquidations. End positions remain open; hypothetical quote-based
  closure reserves and net are reported separately without a strategy stop.

## Red-first regressions and review corrections

Temporary RED logs were captured before fixes. Their outcomes are summarized
here so audit does not depend on temporary local files:

| Log prefix in `/tmp/grid5x-…` | Failing behavior then corrected |
| --- | --- |
| `core-red`, `sizing-red` | Missing replica/sizing implementation |
| `neutral-resting-red` | Already-marketable neutral entries incorrectly rested |
| `floating-loss-red` | Liquidation erased maximum floating loss |
| `range-reentry-red` | Canceled, orderless bot incorrectly returned to running |
| `equity-marks-red` | Coarse segment missed interior equity extrema |
| `funding-order-red` | Funding charged inventory that had already closed before settlement |
| `snapshot-red`, `scanner-red`, `funding-red` | Missing offline scanner/settlement interface |
| `scanner-normalization-red` | Known four-hour funding was excluded instead of normalized |
| `snapshot-boundary-red` | Protected roots could reach filesystem inspection; tests intercepted that call |
| `replacement-red` | Missing cost-aware replacement implementation |
| `replay-red`, `walkforward-red` | Missing report decision; nonfinite metrics could pass |
| `runner-cash-red` | Replacement could leave unallocated cash negative |
| `runner-cost-red` | Range-exit funding and per-coin/end-reserve fields missing |
| `replay-review-red` | Range path truncated; ranking used capital before close fees |
| `replay-marks-red` | Replay ignored critical equity marks |
| `state-export-red` | No serialized final state for operator review |
| `operator-red`, `operator-stale-red`, `cli-red` | Missing form output, stale-state signal, unsanitized invalid DB |

The regression cases and final implementations are committed. Gate logs are
captured per task in the linked transcript, including the last 20 output lines
of `npm run verify` and `npm run verify:secrets`.

## Reproducible commands

No command below is a daemon, LaunchAgent, provider request or exchange order.
Run from this worktree. The snapshot path must be an actual detached SQLite copy
outside the protected directories. A sibling `snapshot.json` must contain:

```json
{"offline_copy": true, "source": "kucoin-public"}
```

This is an operator attestation, not cryptographic proof of snapshot origin.
The reader requires no SQLite journals and uses `mode=ro&immutable=1` with
query-only mode. It rejects links, incomplete schemas and protected roots before
opening. It never creates a copy from live data itself.

```sh
npm run verify
npm run verify:secrets
python3 -m trader.research.grid_cli --help
python3 -m trader.research.grid_cli scan --snapshot /absolute/path/to/offline/copy.sqlite3 --at-ms 1789088400000
python3 -m trader.research.grid_cli sweep --snapshot /absolute/path/to/offline/copy.sqlite3
python3 -m trader.research.grid_cli operator --snapshot /absolute/path/to/offline/copy.sqlite3 --at-ms 1789088400000
```

With serialized `final_state` from a replay and its `state_started_ms`, add
`--state /absolute/path/to/state.json --started-ms <start>` to operator output.
State and market evidence must be current for the requested offline timestamp.
The output includes exact model form values, both grid rates and a replacement
signal; `execution_enabled` is always false. No live/current claim is made from
an old snapshot.

The one-hour synthetic example was generated **after** committed preregistration:

```python
from unittest.mock import patch
from trader.tests.test_grid_runner import Data, START, scanner
from trader.research.grid_runner import run_window
from trader.research.grid_replay import registered_document
registered_document()
parameters = dict(lookback_hours=4, margin_gph=.25, tick_ms=300000)
with patch('trader.research.grid_runner.scan', scanner):
    primary = run_window(Data(), START, START+3600000, parameters)
    null = run_window(Data(), START, START+3600000, parameters, seed=20260911)
```

Output tail from `/tmp/grid5x-synthetic-run.log`:

```text
{
  "completed_grids": 118,
  "completed_grids_per_hour": 118.0,
  "net": 120.04890754602457,
  "max_drawdown": 0.003084220109245507,
  "fees": 37.499999999999915,
  "funding": 0.0,
  "coverage_complete": true
}
Synthetic-only accounting evidence; not two-month acceptance.
```

These numbers are synthetic accounting evidence using injected candidates,
not investment results, measured market throughput, or two-month acceptance.
No real backfill, copied database, historical sweep or current form was available.

## Boundaries and remaining work

This task made no edits to `codex/roadmap-phase-2`, did not access the acceptance
workspace, and did not inspect/control any running process. It did not access
v1, Telegram, LaunchAgents, OpenMausBot export or credentials. No live orders,
paid APIs or Dan-only operator actions occurred. The existing Phase 2 heartbeat
was paused to prevent future scheduled access under the new boundary; the
collector and backfill processes were not stopped or changed.

The earlier Phase 2 checkout incident remains in that phase's evidence; it is
not silently reclassified as an untouched historical checkout by this report.
This separate grid worktree started at `3db5613` and used new bounded agent tasks.

Remaining: supply a detached offline snapshot; establish historical market/funding
coverage; run the preregistered training/holdouts/null; verify actual neutral and
liquidation semantics before a deployment recommendation. Do not loosen the
coverage checks or substitute current quotes for missing historical evidence.
The unmerged source PR is for Claude's audit with these partial statuses.
