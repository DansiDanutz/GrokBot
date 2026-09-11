# KuCoin Futures Grid: semantics, formulas and evidence limits

Official-source review: 2026-09-11; fixture update: 2026-09-12. Governing specification: [Dan's consolidated requirements](specs/grid-kucoin-dan.txt). Observation fixture: [four running bots, 23 symbol rows and RAY neutral creation](../tests/fixtures/kucoin-grid-bots-20260911.json).

This is a source-only paper model and operator recommendation contract. Its deterministic ledger describes simulated fills. It does not observe every actual exchange trade, and the current evidence does **not** establish a calibrated KuCoin replica or satisfy the live-recommender decision gate.

## Official sources and precedence

| Source | Supported conclusion and limitation |
| --- | --- |
| [Futures Grid guide](https://www.kucoin.com/support/20916127546777) | Custom creation uses range, grid count and investment, followed by confirmation. Trading is bounded by the range; existing inventory can remain exposed outside it. Initial exposure is partial, but the guide gives no reproducible allocation formula. |
| [Trading Bot FAQ](https://www.kucoin.com/support/5090571400217) | Futures Grid uses a fixed 0.06% trading fee. Total profit combines grid profit, floating PnL and funding. The FAQ says bots cannot be created through the API. This bot-specific FAQ takes precedence over general fee statements in news articles. |
| [KuCoin Learn Futures Grid](https://www.kucoin.com/learn/trading-bot/kucoin-futures-grid-bot) | Describes leveraged long/short grids and the need to manage liquidation risk. It is a conceptual explanation, not an exchange matching or allocation specification. |
| [SOL setup article](https://www.kucoin.com/news/articles/sol-futures-grid-trading-on-kucoin-step-by-step-setup-guide-and-key-watch-points-for-2026) | Describes the app setup/preview flow and retained positions outside the range. Its 0.02% maker / 0.06% taker claim conflicts with the bot FAQ; do not use the lower maker fee in this model. |
| [KCS setup article](https://www.kucoin.com/news/articles/how-to-trade-kcs-futures-grid-on-kucoin-in-2026-and-must-know-tips) | Gives an equal-price-interval example: 20 grids between 7.60 and 8.40 imply a 0.04 step. Its discussion of modifying fixed ranges is internally inconsistent, so it is not authority for a parameter-edit API. |
| [Neutral direction introduction](https://www.kucoin.com/support/48142946141617) | Confirms a neutral mode for ranging markets and configurable range, count and leverage. It does not specify the initial position ratio, lot allocation or exact completed-arbitrage counting rules. |
| [Reserved Margin and Auto-Add Margin](https://www.kucoin.com/support/48142946142344) | Reserve is additional to the grid investment field, primarily buffers risk, and does not guarantee avoidance of liquidation. Reserved capital prepared at creation differs from auto-add transfers while running. |

An additional [KuCoin BTC blog guide](https://www.kucoin.com/blog/en-how-to-trade-btc-with-futures-grid-complete-strategy-guide-for-beginners-and-pros) describes neutral as starting without a position. Dan's RAY creation screenshot instead records both initial Long and Short positions. The direct observation takes precedence for this captured bot; the blog does not establish its order semantics.

## Capital and grid accounting

For every newly recommended bot, total committed capital is fixed:

```text
B = used_margin + reserved_margin = 1,000 USDT
U = used_margin = B - R
nominal grid allocation = U × leverage
```

The KuCoin investment field receives `U`; its Reserved Margin field receives `R`. Increasing reserve must reduce used margin under this budget. Using `1,000 × leverage` to size positions and then adding reserve would exceed the authorized allocation. Reserve is never multiplied into order sizing or counted again as profit.

The RAY creation fixture explicitly distinguishes **1,000 USDT used + 200 reserved = 1,200 total**. This example is calibration evidence, not a compliant new 1,000-total recommendation. The running screen's Margin field is total committed margin for this observed flow. Accordingly, the earlier SOL running Margin of 10,000 is interpreted as **7,000 used + 3,000 reserve**. Preserve the original field and this interpretation: SOL lacks its creation screen and margin history, so extrapolating the RAY UI semantics to SOL remains an explicit assumption. Do not silently add its reserve twice.

### Interval and app-return approximation

For range bounds `low`, `high`, creation grid count `N`, and price tick `t`:

```text
raw_step = (high - low) / N
step = floor(raw_step / t) × t
p[i] = low + i × step
```

Reject a zero rounded step. With RAY's 1.1–2.0 range, 70 grids and 0.0001 tick, the raw step is 0.01285714 and the observed step is **0.0128**. Repeated ladder differences and prices such as 1.5736 and 1.5864 support downward interval rounding. This can leave an unused remainder at the top of the range; do not invent a wider last interval or allow an order outside the entered bounds. The fixture does not independently specify every endpoint convention.

Dan's requested app-preview approximation is:

```text
f = 0.0006
app_profit_per_grid_pct(p) = 100 × leverage × (step / p - 2 × f)
app_profit_per_grid_usdt(p) = (used_margin / N) × app_profit_per_grid_pct(p) / 100
```

Here leverage is applied once and the percentage is applied to margin per creation grid. `p` must be reported, because return varies across the band. This is an approximate display/sizing calculation, not the realized PnL of an observed contract lot. RAY's rounded step gives about 2.60%–5.22% across 2.0–1.1, close to the captured 2.61%–5.21% display. At 1.574, the formula gives **0.49515 USDT** with 70 grids; the fixture's derived 0.55 is an approximate annotation, not a matching numerical target.

| RAY creation grids | Rounded step | Approx. USDT/grid at 1.58 | Approx. USDT/grid at upper bound 2.0 |
| --- | ---: | ---: | ---: |
| 70 | 0.0128 | 0.49295 | 0.37143 |
| 50 | 0.0180 | 1.01924 | 0.78000 |
| 45 | 0.0200 | 1.27314 | 0.97778 |
| 44 | 0.0204 | 1.33084 | 1.02273 |

Thus “50 grids gives +1” is true approximately near 1.58 and fails at the upper edge. At 1,000 **used**, 5× leverage and these bounds, 44 is the largest integer count satisfying a +1 floor across the band under this formula, both before and after the illustrated tick rounding. A 1,000-total bot with reserve has less used margin and must be solved again. Contract-lot execution must also pass its own minimum-profit check. The goal is **at least +1 USDT after fees per completed grid**, not exactly +1.0000 at all levels; report the minimum and maximum instead of relying on entry-price or mean profit.

### Neutral order structure and actual fill PnL

RAY's creation grid count is 70 but its running order count is **140**: 75 buys and 65 sells. Its captured ladder has two orders at many prices, each 17 lots. It initially holds 544 Long lots and 629 Short lots, exactly `32 × 17` and `37 × 17`, while order history shows 69 entries. These observations support separate Long and Short grid legs with `2N` pending orders, and disprove the earlier guessed `floor(N/4)` seeding rule. They do not establish a universal initial-position percentage or a complete allocation algorithm for every pair and entry location.

Keep creation grid count distinct from open order count; summing green/red orders and calling that `N` is wrong for this Neutral bot. The four older Long rows' sums remain provisional creation counts because their original form is missing. Preserve the observed RAY seed quantities and lot size as calibration inputs; never infer them from the target liquidation price or profit. Neutral needs independently accounted inventory on both legs and losing-side protection at both ends. Its model lower/upper stop fields must not be advertised as an exact app form until the available controls are confirmed.

Contract multiplier converts lots to base units. With `q` base units in an actual adjacent-price cycle `a < b`, both Long buy-then-sell and Short sell-then-buy have:

```text
gross grid PnL = q × (b - a)
fee per fill = 0.0006 × q × fill_price
net grid PnL = q × [(b - a) - 0.0006 × (a + b)]
```

Do not multiply this result by leverage again. Actual contract multiplier, lot increment, price tick and minimum order size must be independently captured. The app-return approximation does not itself explain RAY's 17-lot order allocation. If 17 lots represented 17 base units, a 1.5736→1.5864 cycle would net 0.185368 USDT; this conditional calculation is not a claim about the unknown conversion or a mismatch to be hidden with a fitted factor. Generic margin-based quantity estimates must retain an assumption label until exchange sizing is reproduced.

Long seeds inventory for closing-side orders above entry and leaves opening buys below; Short mirrors the sides and price ordering. Seed closures have separate PnL and fees because an entry-to-grid move may be shorter than a full adjacent-grid cycle. The paper model counts a completed grid only after a regular slot's opening and closing fills. The screenshots do not establish whether KuCoin's arbitrage counter classifies every seed closure identically.

Open-position accounting is additive across lots:

```text
floating PnL = Σ(side × q × (mark - lot_entry)), side ∈ {+1,-1}
funding cash = -Σ(side × q × settlement_mark) × settlement_rate
equity = B + gross grid PnL + seed PnL + close PnL
         - all execution fees + funding cash + floating PnL
```

The displayed completed-grid profit is net of its two fees. Do not subtract those fees twice when reconciling equity. Initial, seed-close and forced-close fees remain visible separately. The spec's UTC 00:00/08:00/16:00 funding schedule is a simulation convention; actual contract schedules must be captured because [KuCoin's contract rules](https://www.kucoin.com/trading-info/futures/futures-details/USUSDTM) allow settlement intervals to change. Missing rates are missing data, not measured zero funding.

## Liquidation, reserve and stops

For the paper stress preview, fill every directional opening slot down to the low edge for Long, or up to the high edge for Short. Seed inventory retains entry cost; newly filled inventory uses its grid price. Let `Q` be full loaded quantity, `C = Σ(q × lot_entry)` its cost, `f = 0.0006`, and `m` the maintenance fraction. With no earlier realized PnL or funding:

```text
Long stress liquidation  = [C × (1 + f) - B] / [Q × (1 - m)]
Short stress liquidation = [C × (1 - f) + B] / [Q × (1 + m)]
```

These follow by setting account equity equal to maintenance notional `m × Q × price`; opening fees reduce equity. A nonpositive Long threshold means no positive-price solution under this simplified scenario. Running liquidation must instead use actual current lots and all accumulated fees, PnL and funding. Neutral requires separate downside and upside scenarios; a net-zero position is not proof that its future grid exposure is safe.

The default fixed maintenance assumption is 0.5%. It is not an exchange risk-tier calculation: [KuCoin's risk-limit rules](https://www.kucoin.com/support/26693511734809) distinguish isolated tiers and cross-margin rules. Exchange mark prices, tiers, reserved-margin treatment, close/liquidation charges and current inventory can change the displayed liquidation estimate. Previews must retain an estimated/unverified label.

The setup requires **both** interpretations of the 10% safety buffer on every losing side. With `W = high - low`, downside distance is `low - liquidation_long` and upside distance is `liquidation_short - high`. Each distance must be at least `0.10 × W` and at least `0.10 × its losing-side edge price`. Report both percentages with their denominators; satisfying one does not imply the other. Place the stop strictly between the losing range edge and liquidation. Evaluate the declared reserve sequence inside the fixed budget, then lower leverage, then narrow the losing side and recompute all sizing; retain the attempted values and rejection reasons.

Within-range adverse movement alone is not an exit trigger. Stops override ordinary replacement rules. Once a stop has closed a bot, absence of a qualifying replacement means **remain closed**, never keep or resurrect it. A range exit without a stop can preserve inventory while the explicit replacement/keep rule is evaluated; report that state and stop distance. Any stop gap is executed at the modeled available price, not retrospectively at the ideal trigger. An emergency is a safety verdict, not a requirement to wait for a better radar candidate. Zero observed paper liquidations is a test constraint, not a promise that liquidation can never happen.

Reducing `N` at fixed bounds **widens** `Δ`. It cannot make an already-too-wide step narrower. If typical one-minute movement is smaller than the step, increasing `N` narrows spacing but usually lowers cycle profit. Treat a step wider than typical one-minute movement as a warning that can lower expected crossing frequency, not an additional hard rejection rule. The scanner ranks its measured/replayed completion rate. Reducing count may help satisfy the profit floor, but must never be described as narrowing the step.

## Four-bot calibration: observations and failed diagnostic

The observed net-profit-per-arbitrage targets are direct fixture ratios. The diagnostic below evaluates Dan's app-return formula at the captured entry, using the unrounded arithmetic step because the historical contract ticks are missing. It assumes each Long green/red sum is its creation count and running Margin includes reserve. These assumptions are explicit; there are no symbol-specific correction factors or quantities recovered from target PnL/liquidation.

| Bot | Assumed used + reserve USDT | Provisional N | Step / entry | Observed USDT/grid | App-formula USDT/grid | Profit error | Observed liquidation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HEMI | 2,500 + 0 | 70 | 0.8719% | 0.88958 | 1.07414 | +20.75% | 0.006928 |
| BTR | 3,000 + 0 | 70 | 0.8747% | 0.88816 | 1.29372 | +45.66% | 0.10039 |
| MOVR | 7,500 + 0 | 90 | 1.0427% | 1.49982 | 4.61359 | +207.61% | 0.495 |
| SOL | 7,000 + 3,000 | 99 | 0.5599% | 1.29217 | 1.55506 | +20.34% | 69.651 |

Error is `100 × (estimate / observation - 1)`. **All four fail** the ±15% profit threshold under these raw-input assumptions. The fixture's derived claim that the formula matches HEMI/BTR within tolerance is not supported by this arithmetic. The revised formula and RAY evidence improve the model specification but do not turn the old observations into a passing calibration.

The ±10% liquidation calibration also remains blocked. A fully loaded edge stress estimate and a current-position screenshot are different scenarios. An independently reconstructed snapshot can be a useful diagnostic if its quantities, entry costs and margin history are supported by observations that do not encode the target; it must be reported separately from the raw-form gate. RAY's creation liquidation preview (0.3238 / 3.1121) and running estimates (0.3246 / 3.1256) should also remain separate timestamped targets, not be blended into one fitted threshold.

Missing independent inputs for the four Long bots include original per-order quantity, contract metadata at creation, initial allocation, margin-add history, exact per-bot capture timestamps, running lots, fee attribution and all fills since start. The first capture spans 14:32–15:15 Europe/Bucharest and includes second HEMI/BTR observations; rows must not be treated as simultaneous. RAY was captured the next day at 11:26–11:27. Do not invert observed PnL to obtain quantity, or observed liquidation to obtain margin, and then present the recovered target as a prediction. Additional observations must be independent of the target being tested.

## Volatility-column verification remains blocked

The fixture records 23 symbols with displayed price, price change, volume and Volatility. Searches of the official guides, support and API documentation did not establish the exact definition of that app column. A candidate research statistic is `100 × (24h_high - 24h_low) / 24h_low`, labeled **candidate 24h amplitude, unverified**. It is not an established KuCoin formula; alternative denominators or return-based definitions remain possible.

The fixture lacks high/low values, an exact timestamp per row and the underlying window. Price and change alone cannot reconstruct extrema. Therefore no honest 23/23 numerical tolerance check can be completed from this fixture. A future check needs synchronized app values, market data and a predeclared formula/tolerance; it must preserve every discrepancy rather than choosing a formula or timestamp to fit each symbol. The expected-grid-rate scanner score is a separate replay statistic, not a substitute for this column.

## Replay and operator evidence boundaries

One-minute OHLC does not identify intrabar order, queue priority, partial fills, spread at execution or actual exchange orders. Replays must disclose their intrabar path and gap rules and report sensitivity to alternate paths. A one-way crossing is not automatically a completed buy/sell cycle. Every reported simulated fill needs bot ID, time, slot, side, quantity, price, fee, reason and resulting position; only an independently captured exchange fill ledger can reconcile real execution.

Under the updated running-Margin interpretation, the four-bot baseline commits **23,000 USDT total**, including SOL's 3,000 reserve inside its 10,000. Used capital is 20,000. SOL's missing creation/history evidence leaves this interpretation provisional; a 26,000-total alternate interpretation must be labeled separately, never silently substituted. Report actual-capital results under the declared interpretation. A 1,000-USDT-per-bot rescaling is a separately labeled normalized counterfactual and is not “Dan's bots unchanged.” Compare windows, exposure time and capital consistently; the two-bot strategy commits at most 2,000 USDT.

Bot ages of roughly two and a half days and a screenshot capture window do not validate two disjoint months. Historical coverage must be measured on the authorized database copy before making any claim about that gate. Missing monthly coverage, failed calibration or unverified essential semantics require a written negative/insufficient-evidence result and **shelve**, even if a short replay is profitable. Neither a smoke test nor synthetic scenarios count as the required market-history evidence.

Manual KuCoin form entry remains the authorized execution interface. Recommendations must expose used/reserved margin, trigger or wait status, both range edges, count, fees, estimated liquidation, required stop controls, current rates and every switch cost. A no-qualified-candidate result is valid; it does not justify unsafe sizing or suppress an already-triggered stop.

## Latest rule: hard stop 5% outside either boundary

The later [Dan correction](specs/grid-kucoin-addendum.md) takes precedence over
the original flexible stop placement. Every new strategy bot, Long, Short or
Neutral, has lower and upper hard barriers at `0.95 × low` and `1.05 × high`.
Thresholds are inclusive. A closer custom stop can trigger first; no custom
field can postpone the hard barrier. Gap execution uses the first modeled
available price, so an exact maximum loss cannot be promised. Exchange-valid
operator stop prices round inward by less than one tick. Both controls must
be supported by the app before calling the form directly executable.

Range evidence uses only closed candles: a five-bar swing pivot requires two
closed bars on each side, so the newest two bars cannot confirm pivots.
Seven-day extrema, grouped pivot touches and recent four-/24-hour evidence
are reported alongside ATR clipping and the final range. Missing suitable
pivots explicitly fall back to observed seven-day extrema. These are auditable
technical levels, not a claim that support or resistance must hold.

The liquidation buffer still must cover the losing-side stop. A range exit
before the hard barrier can request a replacement; at the barrier the paper
engine closes without waiting for one. The four unchanged historical forms
explicitly disable the new rule because their original stop fields are unknown.
