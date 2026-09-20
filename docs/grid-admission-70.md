# Chart-supported capacity for new grid entries

New paper admissions require 70–200 configured grids. The selector searches
confirmed support/resistance pairs in ascending width, rejecting pairs that
cannot fit 70 grids with the existing minimum margin return strictly above 1%
after both modeled fill fees at 5x. It then continues to the next confirmed
pair. This is not permission to invent levels, move a stop arbitrarily, or
relax the floor when no candidates qualify. Grid count is not open-order count:
a Neutral bot can have two books and twice the number of open orders.

Existing 60/40 trend and 50/50 Neutral split checks, fee rates, liquidity,
quantity sizing and liquidation checks remain unchanged. Consequently Dan's
RAY examples still fail the hard split tolerance: resolving whether these
ratios are preferences is a separate policy decision. No new retest thresholds
are invented in this change. Funding remains outside the displayed margin-return
floor; it must not be described as a verified all-in return.

The common `layout_valid` gate rejects stale low-grid radar rows during new
admission. Existing positions do not call this new-admission gate while being
advanced; their saved lines, range, quantity and close rules remain in force.
No live state migration, bankroll reset, service restart or deployment is part
of this PR. An empty qualified radar means wait, not silently admit fewer grids.

Validation includes previously accepted 65/32-grid ranges, a confirmed outer
pair that supports at least 70 grids, original/rounded chart provenance, old
radar rejection and continued processing of an existing 20-grid bot.

## Single current-window check

Read-only CLI run at 2026-09-20T09:43:40.947000+00:00 against the existing phase-2-20260911 market database, with `--no-liquidation-clusters`, no Telegram and no JEV calls. Output was written to `/tmp/grokbot-grid70-radar.json`, never the live radar.

369 rows, 51 passing liquidity, zero trade-section candidates (three majors direction-only). Among the liquid rows: 34 INSUFFICIENT_GRID_ROOM, 11 MISSING_STRUCTURE, 6 ENTRY_SPLIT. This is an observation at one instant, not a backtest or proof that no valid setup will occur. The candidate is not deployed: activation would currently stop new admissions. Ratio interpretation and retest semantics remain unresolved; do not loosen them silently.

Both local gates pass: 1,191 tests (39 Node, 356 paper, 772 trader, 24 team), plus staged-secret scan.

## Isolated split observation (not an admission policy)

`split_mode='observe'` is an explicit comparison option on the pure layout
functions only. All radar and autopilot callers retain the strict default.
It records target/actual buy percentage and deviation; it does not optimize
that deviation, approve an entry trigger, or alter running bots. Invalid mode
names fail explicitly. Structure, minimum count, both-sided orders, price
inside the effective range, fee floor and sizing/liquidation checks still apply.

Dan's fixed 70-grid RAY examples give LONG 18 buy / 52 sell and NEUTRAL
75 buy / 65 sell at the supplied entry prices. Both pass the observed split
check and fail the strict check. The latter differs by one order from the
74/66 screenshot taken at a different price. These fixtures do not establish
KuCoin's quantity formula (the Long preview was 37 lots, actual 39).

Bounded read-only inspection of the six ENTRY_SPLIT rows from the saved
2026-09-20T09:43:40.947Z scan, using the same candle cutoff, finds candidate
layouts in observation mode: ARB 77 grids, FIL 71, NEAR 76, APT 70, JUP 73,
OP 71. Minimum modeled leveraged return per grid ranges 1.0039–1.0615% after
two fill fees, before funding. These are candidate layouts, not six approved
trades or measured profits. No backtest, execution, JEV call or live file write
was performed. Entry timing still requires a separately specified rule.

The active contract previously allowed 12–200 grids. The draft's 70 minimum
is an intentional experimental policy change, not a correction to the deployed
contract; review must resolve this and the split interpretation before adoption.
Neither policy change is deployed by this comparison.


## Funding diagnostic, 20 September 2026

The observation proof now includes `funding_stress`, explicitly a scenario,
not an entry gate, realized profit, expected holding time or all-in forecast.
It evaluates every actual grid pair with both fill fees, at 5x, deducting
one adverse funding settlement per base unit valued at the upper chart bound.
Favourable receipts are ignored; Neutral is checked without assuming the
opposite book remains open to offset its paying book. The helper accepts an
explicit settlement count for fixtures; it never converts grids/hour into
holding time. Missing/nonfinite funding is UNKNOWN_RATE, never zero.

Formula: net/unit = sell - buy - fee_rate*(buy+sell) - adverse_funding/unit;
margin return = net/unit divided by the pair's entry price, multiplied by
leverage and 100. Leverage changes the margin return, not the cash fee or
funding per unit. Cash profit for a known base quantity is quantity*net/unit;
contract multipliers must be applied before using lot quantities. Funding uses
position value times rate, with positive rate paid by Long, negative by Short:
[KuCoin funding documentation](https://www.kucoin.com/support/26695933047705).

Same saved scan as above (09:43:40Z), funding observed 09:36:24Z. These are
historical snapshot diagnostics, not current market recommendations. For a
fixed rate and an in-range mark, the high-bound assumption is conservative;
rate changes, additional settlements, execution slippage and out-of-range
prices can cost more. No historical performance replay was run.

| Coin | Grids | Fees-only min % at 5x | One adverse settlement min % | Above 1% |
| --- | ---: | ---: | ---: | --- |
| ARB | 77 | 1.0058 | 0.9556 | No |
| FIL | 71 | 1.0060 | 0.9558 | No |
| NEAR | 76 | 1.0615 | 1.0112 | Yes |
| APT | 70 | 1.0042 | 0.9540 | No |
| JUP | 73 | 1.0039 | 0.9783 | No |
| OP | 71 | 1.0093 | 0.9591 | No |

All six were Long. Snapshot rate was +0.01% per 8h except JUP +0.005%
per 4h. Five failing this scenario proves fee-only >1% is not sufficient
to promise >1% after funding; it does not establish their actual holding cost.
Do not activate the draft as a finished entry upgrade. Entry confirmation,
funding horizon and the grid-floor/split contract remain separate decisions.

Validation for the funding addition: tests written first (six missing-helper
failures), then 25 targeted spacing/layout tests pass. Full gates: 1,201 tests
(39 Node, 356 paper, 782 trader, 24 team) and staged-secret scan.


## Fixed-range funded count selection

The pure selector accepts `funding_settlements` only with explicit observation
mode. It tries smaller counts within the same supplied structural boundaries,
still requiring 70–200 grids and the existing lot/liquidation checks. Unknown
funding rejects with UNKNOWN_FUNDING_RATE. Counts that fail the scenario are
recorded as FUNDING_RETURN_BELOW_1_PERCENT; the count is never silently lowered
below 70. Default callers do not pass this option and remain unchanged.

Reusing the six previously identified rounded pairs and the saved radar rows
at the same cutoff (not a new chart assessment or performance replay), the
one-adverse-settlement scenario yields:

| Coin | Previous count | Funded count | Scenario minimum % at 5x |
| --- | ---: | ---: | ---: |
| ARB | 77 | 74 | 1.0067 |
| FIL | 71 | Rejected | Below 1% even at 70 |
| NEAR | 76 | 76 | 1.0112 |
| APT | 70 | Rejected | Below 1% at 70 |
| JUP | 73 | 71 | 1.1245 |
| OP | 71 | Rejected | Below 1% even at 70 |

Tick rounding causes discrete jumps in the interval and return, especially
JUP. These numbers establish conditional mathematical capacity only. They
are not current recommendations, confirmed entry timing, or an approval to
adopt the 70-grid policy. Longer holding periods or a changed rate can still
fail the floor; a scenario is not a guarantee. Existing state is untouched.

Four new tests were run failing first, then passed: fixed-bound maximum count,
no feasible 70-grid layout, missing funding rejection and explicit-mode guard.
Full validation: 1,205 tests (39 Node, 356 paper, 786 trader, 24 team), plus
staged-secret scan. No production activation.
