# Grid policy v2 — 2,400-USDT bankroll and fresh paper window

**Source policy updated; paper execution has not started.** Existing timers,
positions, balances and records are unchanged. The future 48-hour clock begins
only on the revised runner's real start event. No start timestamp was invented.
Research remains **shelved for live recommendation**, with the original
calibration and historical-data gaps still unresolved.

This branch, `codex/grid-kucoin-policy-v2`, starts from PR #14's frozen
`165296ccb744626ca0a18756903a913e8cfbe2eb`. PR #14 remains untouched for its
auditor. This follow-up is tracked in [issue #13](https://github.com/DansiDanutz/GrokBot/issues/13)
and is handed off as a separate, unmerged stacked PR. The frozen final head and
exact-head Ubuntu/macOS CI are recorded in its final audit comment.

## Check against Dan's latest instructions

| Requirement | Before this revision | Revised source behavior |
| --- | --- | --- |
| Trading venue | Modeled KuCoin Futures Grid, manual app forms | Same venue; source never controls an account or pretends simulated fills are exchange fills. |
| Radar | Five candidates | Ten by default; configurable five through ten. Shows expected grid rates and separately labeled high/low amplitude. KuCoin's exact Volatility column remains unverified. |
| Leverage | Setup searched 3–6x | Fixed **5x**; conflicting leverage inputs are rejected. |
| Bankroll | 1,000 total per bot, divided into used/reserve | **1,000 used + 200 reserve = 1,200 per bot**, maximum two = **2,400 USDT**. Order sizing leverages only used margin. |
| Entry selection | Ranked crossing proxy with setup eligibility | Positive projected net grid income after opening-fee budget, safe eligible +1 grid floor, support/resistance ranges, direction and trigger rules; best funded setup then a different second one. |
| Replacement | Required held low-rate/direction or range trigger | May replace the **worse incumbent** when a higher-rate challenger improves projected grid income after close loss and entry fees. At most one ordinary replacement per hour. |
| Empty second slot | Operator could omit its recommendation | Operator and replay share funded entry selection; a vacant second slot is filled only with an eligible setup and sufficient known cash. |
| Normal stop | 5% beyond either edge | Retained for all three modes, with valid ticks rounded inward. |
| Liquidation-risk fallback | None | Latch the affected side to **1% outside its edge** when modeled clearance is insufficient. If 1% is also unsafe or crossed, close immediately. Never loosen the latched stop. |
| Evaluation timer | Existing run's original audit epoch | Separate **48-hour** paper window, bound to the revised policy and source revision, starting only at actual paper startup. Old results are preserved. |

The independent review found and corrected unknown-cash replacement proposals
and divergent entry selection between operator and replay. A later propagation
check also corrected both preview stop ticks and the operator's radar-size
parameter. No remaining blocking issue was found in that bounded recheck;
this is not certification of KuCoin parity or profitability.

## Exact capital and risk semantics

Trading margin totals 2,000 USDT; reserve totals 400 USDT. The implementation
cannot silently lower leverage, resize the fixed allocation, or assume another
deposit. It narrows an unsuitable range or rejects the coin. Having two slots
does not justify creating a second unsafe or unfunded position.

The operator requires a fresh paper-cash snapshot to issue funded proposals:
optional `capital: {available_cash_usdt, asof_ms}` in its running-forms JSON.
`asof_ms` must equal the requested report timestamp; cash must be finite and
nonnegative. Missing cash leaves radar research visible but withholds funded
entry/replacement forms. The figure means cash available **after** allocations
already released at that timestamp, preventing terminal equity from being
added twice. A paper ledger must supply it; it is not inferred from screenshots
or an omitted running bot. Available cash is reserved once for a vacant slot
before considering another replacement.

The default six-hour comparison is explicit, configurable and unvalidated:

```text
incumbent income = realized grids/hour × observed mean grid net × horizon
worst score = incumbent income + executable close net PnL
challenger income = crossing proxy/hour × candidate net grid floor × horizon
advantage = challenger income - opening fee budget
            - max(0, closing loss including close fees) - incumbent income
```

A challenger needs strictly more expected grids/hour and positive advantage.
Closing profit does not subsidize a weak challenger. The closed slot must fund
another 1,200-USDT allocation without an external deposit. Forecasts cannot
promise future crossings, fills or profit. Exact bid/ask close costs and quote
age remain visible.

Adaptive risk protection checks current inventory **and** a hypothetical
monotonic fill projection at the proposed stop with an additional
**1% of the range-edge price** clearance. Equity there must exceed maintenance
plus modeled market-close fees. That extra clearance is a declared research
assumption, not an exchange rule. Checks run before price movement, after each
fill and after funding. Projected fills never enter the actual paper ledger.
Stops tighten per side and never loosen; a failed tightened check triggers
`stop_loss` with `protective_reason: risk_protection`.

Example: lower range boundary 90 gives a normal stop at 85.5 and a tightened
stop at 89.1, before tick adjustment. This **1% outside the range** differs from
the existing **5% distance to liquidation price** early warning. The latter
continues to request verification of tightened protection or stopping; it does
not recommend additional reserve beyond the fixed allocation.

Gaps can still cross liquidation before a stop fills. Such outcomes are
recorded as liquidation and fail acceptance. No “cannot be liquidated” claim
is made. Intrabar closing spread, exchange risk tiers and allocation parity
remain limitations inherited from the first study.

## New timer: prepared, not running

[paper_evaluation.py](../../trader/research/paper_evaluation.py) provides pure
state transitions for a future approved paper runner; it does not install or
start one. No existing dashboard clock is changed.

1. `arm_evaluation` seals the policy and a full frozen source revision, preserving
   the previous run ID. It returns `awaiting_paper_start`, with no start/deadline.
2. `start_evaluation` accepts only `paper_run_started`, a different run ID, the
   matching seal, and a fresh nonfuture first observation. Source changes,
   schedules, backtest completion and the first trade do not start the timer.
3. The first audit is exactly **start + 48 elapsed hours**, independent of civil
   daylight-saving changes. Repeated matching start events are idempotent;
   altered starts and inconsistent persisted deadlines are rejected.
4. The boundary marks an audit due. It does not stop trading for profit or erase
   previous records. A future approved runner/report scheduler must invoke the
   hook and consume the audit boundary; no deployment happened in this task.

The new [preregistration](../../research/preregistration/grid-kucoin-policy-v2.json)
explicitly retains `started_at_ms: null`, `first_audit_at_ms: null` and
`status: awaiting_paper_start`. There is no genuine new 48-hour result yet.

## Evidence and remaining gate

Task commits and both gate outputs are in
[grid-kucoin-policy-v2-gates.md](grid-kucoin-policy-v2-gates.md).
[Red-first records](grid-kucoin-policy-v2-red.md) cover fixed capital/leverage, adaptive stops after fills
and funding, neutral/short symmetry, gap liquidation, non-loosening, both stop
ticks, cash feasibility, vacant slots, ranking, radar size and sealed clock
lifecycle. Independent recheck passed 46 focused tests. Final source gates pass **38 Node +
279 existing paper + 221 grid = 538 tests**, plus the secret gate.

The revised preregistration has 12 combinations per holdout (horizon 4/6/12,
direction threshold .12/.24, radar five/ten). On the same detached copy, all
**24 trial records remain `not_run_coverage`**; no parameters were selected.
The result is **shelve**. This check reads the copy only and starts no paper
runner. [Machine-readable result](grid-kucoin-policy-v2-sweep.json).

The original report at frozen PR #14 contains the verified 11,567,789 candle
rows, quote/book gaps, 3,751 unknown-period funding rows and failed Long/Neutral
calibration. Those limitations were not solved by changing policy or capital.
No fresh market collection, paid API request or real account access occurred.

No v1 runtime, Phase 2 checkout/acceptance state, existing LaunchAgent,
ZmartyChat-paper-grid, Telegram bot, OpenMausBot export, credential or old timer
was modified. The PR stays unmerged; after the final CI/head comment, remain
idle for Claude's audit. No Phase 3 work is included.
