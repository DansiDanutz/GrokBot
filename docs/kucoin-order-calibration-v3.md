# KuCoin order-quantity evidence, v3

Dan’s latest rule requires **strictly positive cash after both fill fees**, with
exit on either exact range boundary. The older +1-USDT comparisons below are
historical diagnostics; they no longer define success or admission.

Dan authorized inspection of his signed-in Chrome account on 11 September 2026.
The redacted fixture records only strategy parameters and quantities from History
→ Parameters, plus three RAY Order History cycles. Account identifiers, bot IDs,
balances outside these configurations, cookies and credentials are excluded.
No order was opened, stopped or changed. Early unsettled form-preview output
was excluded; later settled unsubmitted previews are recorded separately below.

| Configuration | Grids | Observed lots/order | Contract multiplier | Current used margin | Reserve |
| --- | ---: | ---: | ---: | ---: | ---: |
| HEMI Long 4x | 70 | 114 | 100 | 2500 | 0 |
| BTR Long 4x | 70 | 68 | 10 | 3000 | 0 |
| MOVR Long 6x | 90 | 1698 | 0.1 | 7500 | 0 |
| SOL Long 5x | 99 | 27 | 0.1 | 7000 | 3000 |
| RAY Neutral 5x | 70 | 17 | 1 | 1000 | 200 |
| BTR Long 5x, separate archived bot | 82 | 171 | 10 | 3000 | 2000 |

Multipliers and ticks come from the detached public-market-data copy, so they
remain retrospective contract-specification assumptions for older bots. Long
intervals are inferred from tick-rounded ranges; RAY's 0.0128 interval is directly
observed in its order ladder. KuCoin labels the collateral **Current Margin**;
original creation amounts and subsequent amendments are not established here.

## What is verified

For base quantity `q = lots × multiplier`, a buy at B and sell at S nets
`q × (S − B) − 0.0006 × q × (B + S)`. Leverage is already reflected in order size;
it must not multiply this PnL again. RAY's observed 17-lot cycles reproduce:

| Buy | Sell | Calculated net USDT | KuCoin displayed |
| ---: | ---: | ---: | ---: |
| 1.5992 | 1.6120 | 0.18484576 | 0.1848 |
| 1.5864 | 1.5992 | 0.18510688 | 0.1851 |
| 1.6248 | 1.6376 | 0.18432352 | 0.1843 |

The earlier four Long average profits also fall inside their respective
quantity-based, fee-deducted grid-profit bands. This uses independent observed
quantities; it does not invert profits to invent quantities. It supports the
accounting formula, not a universal allocation formula or liquidation parity.
KuCoin describes its displayed profit/grid as fee-deducted in its
[parameter guide](https://www.kucoin.com/support/21959472633113).

## What remains blocked

Entry-price allocation, upper-price allocation, and upper-price allocation with
an explicit two-fill fee allowance all fail to explain every Long observation.
The upper-price candidates happen to return RAY's 17 lots, but this does not
validate candidates that fail other configurations. RAY was already observed;
it is a retrospective cross-mode check, not an untouched statistical holdout.

KuCoin documents reserve as additional to grid investment and describes margin
additions while a bot runs. It does not publish an allocation equation in the
reviewed [margin guide](https://www.kucoin.com/support/48142946142344).
Those facts justify checking creation and amendment history; they do not prove
that quantities are unchanged after every amendment.

No new quantity rule is adopted. Automatic quantity-dependent grid-count selection and
its associated expected income remain unvalidated until a rule predicts the
independently observed quantities from known creation inputs. Existing sizing in
v3 candle replays is labeled an uncalibrated sensitivity model, not an executable
KuCoin recommendation. The paper evaluation clock remains unstarted.

## Apply the cash formula to every observed configuration

The same accounting applies to Long, Short (sell first, buy back later), and each
completed Neutral leg. A fill is not a completed grid. Keep seed-position PnL,
floating PnL, funding and switching costs separate from completed-grid income.
The following bands use each independently observed order quantity and the whole
configured grid ladder; Long intervals remain inferred as described above.

| Observed configuration | Net USDT/completed grid, after two fees | Positive after fees? | Legacy ≥1 check |
| --- | ---: | --- | --- |
| HEMI Long 4x, 114 lots | 0.84505008–0.93189072 | Yes | No |
| BTR Long 4x, 68 lots | 0.83070976–0.91629184 | Yes | No |
| MOVR Long 6x, 1698 lots | 1.33836360–1.51971000 | Yes | Yes |
| SOL Long 5x, 27 lots | 1.15120116–1.34361828 | Yes | Yes |
| RAY Neutral 5x, 17 lots | 0.17701216–0.19502944 | Yes | No |
| Archived BTR Long 5x, 171 lots | 0.57554496–0.65532672 | Yes | No |

These describe captured configurations, not a fresh inventory of the account.
Passing a cash floor does not establish net profitability or liquidation safety.
Acceptance tests exercise the existing engine, preview, tracker and portfolio
accounting in all three modes, including fixed-quantity leverage invariance.
No live bot was modified and no paper runtime was deployed.

The supplied latest-100 RAY history contains 25 numeric grid-profit rows totaling
4.6195 USDT. All 100 rows report 17 lots. Duplicate-looking rows are preserved:
there are no event IDs or side labels with which to deduplicate or pair fills.
The positive profit displays are consistent with truncating to four decimals,
including 0.18458464 displayed as 0.1845. This is a partial grid-profit display,
not lifetime PnL or proof of each row's opposite fill.

## Settled unsubmitted form observations

The later Chrome previews used RAY, range 1.1–2, used margin 1000 USDT,
reserve 200 USDT and 5x. These were draft forms only. Prices moved between
observations; they are not simultaneous quotes or actual order allocations.

| Direction | Grids | Interval | Quoted lots/order | Displayed minimum investment | Profit/grid preview | Liquidation preview |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| Long | 70 | 0.0128 | 41 | 24.1798 | 2.61%–5.21% | 1.0537 |
| Short | 70 | 0.0128 | 38 | 26.2768 | 2.61%–5.21% | 2.0953 |
| Neutral | 70 | 0.0128 | 15 | 62.6376 | 2.61%–5.21% | 0.3234 / 3.1118 |
| Neutral | 29 | 0.0310 | 38 | 25.9999 | 7.26%–13.48% | 0.3195 / 3.1114 |
| Neutral | 30 | 0.0300 | 37 | 26.9012 | 7.00%–13.02% | 0.3035 / 3.1281 |
| Neutral | 31 | 0.0290 | 36 | 27.7672 | 6.75%–12.57% | 0.3107 / 3.1209 |

The current Neutral preview's 15 lots differs from the independently observed
running bot's 17 lots. It must not overwrite the actual quantity. The expression
`floor(used margin / displayed minimum investment)` matches these draft counts,
but minimum investment is another KuCoin output; this is not a prediction from
form inputs or an identified allocation algorithm.

For a candidate grid count N and independently obtained order quantity q(N), the
current cash test is strict positivity at every adjacent pair:

`min over adjacent buy/sell prices: q(N) * multiplier * ((sell - buy) - 0.0006 * (buy + sell)) > 0`.

For fixed base quantity Q, buy price B and an explicit positive historical
cash target T, the step floor is `(T / Q + 2 * 0.0006 * B) / (1 - 0.0006)`.
The new zero-floor rule instead requires a tick-aligned step **strictly greater**
than `2 * 0.0006 * B / (1 - 0.0006)`. Exact break-even does not pass.
There is no additional arbitrary USDT epsilon.

Choose the largest feasible N only after evaluating its own quoted quantity and
rounded interval. For the archived one-USDT comparison, the observed 30-grid Neutral draft gives 1.021866–1.060494
USDT per completed cycle; the 31-grid draft gives 0.9582696–0.9958536. Thus 30
passes the old one-USDT floor and 31 fails it; both have positive modeled
cycle cash under the new rule. This is not an exhaustive
search, a current recommendation, or a universal 30-grid rule. Fees, funding,
contract rules and prices must still be checked for a real configuration.

Radar income/hour uses crossing-rate estimates times quantity-based minimum
net USDT/grid. The KuCoin percentage preview is displayed separately and is not
substituted for actual cash income. Until independent allocation calibration
passes, automatic forms and historical results remain research only.

The new source policy uses exact decimal arithmetic for cash admission and
cycle classification. It exits at low/high inclusively and does not wait 5% or
1% outside the range. Closed positions retain historical risk diagnostics without
blocking a funded new entry. Actual liquidation is still a trial failure.
