# Dan’s Neutral fixture and +1 USDT correction

The RAY screenshots supplement the consolidated spec. Observed creation:
Neutral 5x, 1.1–2.0, 70 grids, displayed interval 0.0128, used margin
1000 USDT plus reserve 200 USDT. Preview: 2.61%–5.21% per grid,
liquidation 0.3238 / 3.1121. Post-creation: 544 lots long, 629 lots short,
17 lots per order, 140 open orders, 75 buys / 65 sells, zero arbitrages,
unrealized −2.39 USDT and total profit −2.4 USDT. The later liquidation
values are 0.3246 / 3.1256; they are distinct observations.

The requested approximation is:
`percent = 100 * leverage * (interval / price - 2 * 0.0006)`;
`USDT per grid = used_margin / grids * percent / 100`.
Report both in every setup and verify against the supplied fixtures.
Neutral preview percentage tolerance is 0.3 percentage points and each
liquidation tolerance is 10%. Model simultaneous legs and two orders per
interval. Do not infer unobserved execution prices from the PnL target.

The requested roughly 50 RAY grids is a centre-price example. The governing
“every completed grid” requirement remains a minimum across the whole range;
document any numerical conflict. The scanner must use the setup’s accepted
interval and never use a finer grid to inflate its crossing score.

All source-only, branch, TDD, verification and audit boundaries remain in force.

## Later stop and range correction

Dan subsequently requires a hard close when the bot is 5% outside its range.
This applies beyond either boundary for all three modes: `low × 0.95` and
`high × 1.05`. It is independent of the hourly replacement candidate test;
no challenger is required to honor the stop. Inside-range movement remains
part of normal grid behavior. App stop prices round inward to an admissible
price tick, so they cannot wait beyond the threshold. Range selection must
show its support/resistance analysis. The unchanged historical-form baseline
retains its observed forms as a separately labeled comparison.
