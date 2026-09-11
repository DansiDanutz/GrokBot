# KuCoin Futures Grid: product semantics and paper assumptions

Research date: 2026-09-11. This source-only experiment follows Dan's corrected
specification. Its primary metric is completed, paired grids per hour, with a
target of 1 USDT per pair after trading fees. Reaching a profit amount never
stops the bot. The experiment is an approximation of the product, not verified
exchange-equivalent execution.

## Verified product behavior

KuCoin's neutral direction targets range-bound movement and exposes pair,
range, grid count and leverage configuration. The neutral guide does **not**
specify its initial long/short allocation or exact order-matching algorithm.
Its discussion of returns includes trading fees, funding and position PnL.
[Neutral direction guide](https://www.kucoin.com/support/48142946141617).

The older tutorial says the initial opening **may** be about 50%; it does not
define an invariant half-position rule or a neutral allocation. It documents
1–10x leverage and long buy-low/sell-high versus short sell-high/buy-low cycles.
[Beginners tutorial](https://www.kucoin.com/support/10560658630681).

The documented arithmetic interval is `(high - low) / placed_orders`: four
intervals produce five price levels. Profit/grid deducts transaction fees.
Out-of-range prices halt further grid trading. Trigger entry is optional.
KuCoin's creation preview estimates liquidation assuming all directional grid
orders fill; its running-position estimate uses the current position instead.
The product offers stop-loss and take-profit settings; this experiment disables
both discretionary targets under Dan's specification.
[Parameter description, Futures Grid section](https://www.kucoin.com/support/21959472633113).

KuCoin lists a fixed 0.06% trading fee for Futures Grid. The model charges
`0.0006 × executed notional` on every fill, including initial and market-close
fills, with no maker discount or KCS discount.
[Trading Bot Fee Rules](https://www.kucoin.com/support/5310983040025).

Exiting a Futures Grid realizes profit or loss and returns funds from the bot
account. The guide does not establish an exact exit slippage model. Our explicit
market-close rule comes from Dan's specification.
[Futures Grid guide, exiting the bot](https://www.kucoin.com/support/20916127546777).
Market orders can consume several price levels and can be partially canceled by
the exchange's price protection. A candle-price full close cannot prove this
execution outcome.
[Order Type, Market Order](https://www.kucoin.com/support/26686562304793).

## Exact paper conventions

| Input | Research value or convention |
| --- | --- |
| Pair | A KuCoin USDT perpetual symbol; preserve its exchange identifier |
| Direction | `long`, `short`, or `neutral`; default `neutral` |
| Low/high | Positive prices, low strictly below high |
| Grid count | Number of arithmetic intervals; one more boundary price than intervals |
| Spacing | Arithmetic only |
| Leverage | 1–10x inclusive; default 5x |
| Investment | Margin in USDT; default 1,000 |
| Trigger | Optional entry price; no position before activation |
| Take-profit | Absent; neither per-grid nor cumulative profit terminates a bot |

At activation, the paper model uses 50% of its leveraged notional as gross
initial inventory. Long uses that inventory long; short uses it short. Neutral
splits it equally: **25% long plus 25% short**, so initial net exposure is zero
and gross exposure is 50%. The remaining capacity supports grid orders.
This symmetric allocation is an explicit modeling choice, **not a rule verified
from KuCoin**. Both opening sides incur fees. Contract lot rounding and exchange
margin reservations may change actual initialization and quantities.

A grid completes only when its entry inventory is matched by the opposing
exit fill. A price crossing alone is not a completed grid. Opening inventory
may realize a different profit on its first exit because its entry was the
activation price. Reports must preserve those actual profits, never assign
1 USDT automatically to every fill or initial-inventory close.

Grid orders operate inside the fixed range. Outside it, the replica adds no
new grid orders; the controller records a range exit and closes inventory at
the modeled market price before considering replacement. Otherwise only the
registered replacement rule can voluntarily close a running bot. Forced
liquidation remains possible; it is not a profit target or an optional stop.

## Sizing and the 0.52% example

Investment, leverage, center and profit target do not uniquely determine all
of range, step and grid count. The helper therefore accepts a configurable grid
count, defaulting to 20. With margin `M`, leverage `L`, center `C`, grid count
`G`, target `T` and fee `f = 0.0006`, its unrounded quantity convention is
`q = M × L / (G × C)` base units per grid. For a completed pair entered at
`p` and exited at `p + d`, net trading profit is:

```text
q × [d - f × (2p + d)]
```

For `M=1000`, `L=5`, `G=20`, `T=1`, the per-grid center notional is 250 USDT.
Solving at `p=C` gives `d/C = (1/250 + 2f)/(1-f)`, approximately **0.5203%**.
Arithmetic spacing has larger fee notionals at higher prices. Guaranteeing
the target for the highest adjacent pair in the symmetric range requires
`d = (T/q + 2fC)/(1-f(G-1))`, approximately **0.5260%** of center for 20 grids.
Set `low=C-Gd/2` and `high=C+Gd/2`; reject nonpositive low. These are model
derivations, not KuCoin's unpublished sizing or reservation algorithm.

The preview must distinguish grid count, boundary levels and currently pending
orders. It reports fee-adjusted pair profit and an explicitly approximate
liquidation estimate, not a verified exchange quote. Exact liquidation depends
on contract maintenance tiers, mark price, margin, funding and execution state;
missing tier evidence prevents exchange-parity claims.

## Funding: requested scenario versus exchange schedule

Dan's scenario settles funding every eight hours. Use UTC 00:00, 08:00 and
16:00 boundaries with signed cash flows on open inventory; positive rates debit
longs and credit shorts. Funding is separate from the 1 USDT trading-fee target.
KuCoin documents these eight-hour boundaries and also four-hour settlements.
[Funding Rate Tutorial](https://www.kucoin.com/support/26695933047705).

**Eight hours is not universal.** KuCoin's current guide says major pairs use
eight hours, most other pairs four hours, with possible temporary one-hour
settlement. Settlement uses position value at mark price. An eight-hour
equivalent rate used by the scanner must retain its source interval and
normalization provenance; missing intervals are unknown, not assumed eight.
A fixed-eight-hour scenario cannot validate actual cash flows for contracts
with different schedules.
[Funding Fee](https://www.kucoin.com/support/26686295987353).

## Scanner, replacement and evidence limits

The scanner reports 24-hour and four-hour step-crossing rates separately. These
are backward-looking activity estimates, not fill forecasts. Only completed
candles available at the decision timestamp may contribute. Spread, turnover,
depth, listing age and normalized funding are the specified eligibility filters;
missing or stale evidence fails eligibility instead of adding an invented value.
Top-of-book depth must be converted from contracts using the contract multiplier
before comparison with one grid's base quantity or notional.

The controller compares the candidate's expected rate with the running coin's
completed-grid rate over configurable `N` hours. Its registered margin must
cover closing floating inventory and fee costs in USDT; both rates, realized
floating PnL and switching costs are recorded. Floating loss is an economic
cost of switching, even when preceding grid profits are positive. Bookkeeping
must not subtract the same close fee twice. Range-exit replacement is recorded
separately from a rate-improvement replacement.

One-minute OHLC does not reveal intraminute ordering, queue position, partial
fills, spread history, book depth or mark-price liquidation paths. Replay must
state its deterministic intrabar path and executable-price assumptions. Missing
bars cannot be filled with invented prices; current listings or books cannot
be projected into past months. A random-coin null must share the same costs,
eligibility, grid rules and replacement logic, with a recorded random seed.

Before any sweep, preregister the decision rule: positive net after funding and
switch costs in two disjoint months, with max drawdown below 10%; otherwise
`shelve`. Incomplete historical eligibility, funding or execution evidence
prevents a deployment recommendation even if a synthetic scenario passes.

## Operator boundary and provenance

Operator output is a form-value proposal plus an optional `replace now with X`
signal showing both rates. It does not submit orders or manage an account.
The public API documentation reviewed lists market data, orders and positions,
but no Futures Grid bot creation/stop route was found. This is a bounded finding
from the published catalog, not proof about private/internal APIs. No private
route is inferred or called.
[KuCoin API documentation](https://www.kucoin.com/docs-new).

All citations above were read from KuCoin's official pages on the research date;
the neutral guide was additionally read through the agent-reach Jina reader.
Only short paraphrases and original model derivations are stored here. No
account access, paid request, runtime access or source-data mutation was needed.
