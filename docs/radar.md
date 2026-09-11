# KuCoin grid radar

The radar is a read-only operator aid. It reads the existing KuCoin market SQLite database, prints ranked tables, and writes `radar.json`. It cannot place, edit, or stop an exchange order. Dan enters any selected setup in KuCoin by hand.

## Run it

```sh
cd ~/ZCodeProject/GrokBot
python3 -m trader.radar \
  --database ~/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3 \
  --json ~/Sandbox/grokbot/radar/radar.json
```

Open `/radar` on the existing paper server. The publisher includes the same page at `/radar` and copies a bounded public DTO to `/data/radar.json`.

The uninstalled template `config/launchd/com.danslab.trader-radar.plist.example` runs at minute `05` each hour. Before considering installation, replace `REPLACE_WITH_DAN_CHAT_ID` and manually run its wrapped command once. The wrapper reads `DLS_TELEGRAM_BOT_TOKEN` from `~/.config/danslab/credentials/telegram.json`; the token never belongs in source or a plist. Telegram sends the top three per section only when their symbol identities change.

## Columns and sections

- **Direction:** `LONG` when 4-hour and daily EMA20/50 agree upward, `SHORT` when both agree downward, `TURNING-UP` or `TURNING-DOWN` when only the 4-hour trend has turned, otherwise `NEUTRAL`. With fewer than 20 daily bars, the daily fast EMA falls back from 20 to 10 periods; with fewer than 50 daily bars, the daily slow EMA falls back from 50 to 20 periods.
- **ATR 1h %:** 14-period hourly average true range divided by current price. It represents oscillation, not a forecast.
- **Range:** directional candidates use current price, 4-hour ATR, and the 7-day extreme. Neutral candidates use the 7-day low and high.
- **Step % / grids:** the arithmetic grid interval and the range width divided by that interval, capped at 200.
- **Expected grids/h:** the fixed coefficient times hourly ATR% divided by step%. Ranking multiplies this estimate by liquidity, capped at full weight from 8 million USDT turnover.

The output shows Majors (BTC, ETH, SOL direction only), Turning up, Turning down, Long, Short, Neutral candidates in the middle 25–75% of their 7-day range, and Movers with an absolute 24-hour move above 15%. Each section is capped at eight rows. Candidate sections require at least 3 million USDT 24-hour turnover, spread at or below 0.15%, seven days of listing age, and both latest ticker and book inputs no more than 120 minutes old. `snapshot_age_min` reports the age of the older required input.

## Fixed live-bot constants

These are named constants in `trader/radar/radar.py`; there is no fitting, replay, or research code:

| Turnover | Step | k | Expected grids/h |
| --- | ---: | ---: | --- |
| below 50M USDT | 0.80% | 1.90 | `1.90 × ATR1h% / 0.80` |
| 50M USDT or more | 0.52% | 0.45 | `0.45 × ATR1h% / 0.52` |

Dan fitted these constants from the live HEMI, BTR, MOVR, and SOL grid bots observed on 11 September 2026. They are operational heuristics and expected values, not guaranteed fills or profit.

## Shared watchlist score

Private `radar.json` rows now also carry `score` (clamped to 0–100) and
`score_parts`, containing only fixed codes and numeric measurements/points.
`trader/radar/scoring.py` is shared with the paper autopilot: oscillation 30,
trend clarity 20, turnover 10, spread 5, range room 15, funding 10 and stability 10,
minus explicit mover, young-listing, stale-data and major-low-yield penalties.
Turnover points rise linearly from zero at 3M to ten at 30M USDT; spread points fall
from five at 0.05% to zero at 0.15%. Separate LIQUIDITY_TURNOVER and LIQUIDITY_SPREAD
codes preserve both inputs. Invalid/nonfinite scoring inputs are rejected.
The existing radar section ordering and rank_score remain unchanged. Phase C's
paper watchlist will render these reasons; the current public radar DTO is unchanged.

The shared k(step) implementation lives in `trader/radar/rates.py`: the standard
coefficient is `1.9 * sqrt(step_pct / 0.8)`; at turnover >=50M it is
`0.45 * sqrt(step_pct / 0.52)`. The table above shows the unchanged base-step values.

Entry boundaries use repeated pivots in the last seven days of completed hourly candles: two candles confirm each pivot, two distinct tests at least three hours apart confirm a cluster, and clustering tolerance is the smaller of 0.25 hourly ATR and 0.5% of price. Every direction requires the nearest confirmed support below price and resistance above price. Two completed closes beyond a former resistance/support allow it to change roles. Setups without both required levels are withheld. These are modeled levels, not a guarantee of support. Entered boundaries remain fixed; the paper bot exits on the first observed boundary touch, including a loss.


## Range capacity and fees — Dan clarification, 11 September 2026

New entries in every direction require both confirmed support and resistance;
their fixed prices define the range. Do not expand a range to accommodate a
requested count. Choose the number of geometric intervals from that range and
the target step (0.8%, 0.52% on majors, 0.45% for Neutral), capped at 200.
The actual step is 100 × ((high / low) ** (1 / grids) − 1); use it for the
expected grids/hour estimate and the bot, rather than the target step.

Validate every adjacent pair: quantity × (sell − buy) − quantity × fee_rate ×
(buy + sell) must be positive and at least 20% of the two fees. The shared
configurable safety-margin default is 0.20; the paper fee is 0.0006 per fill.
For geometric spacing this inequality is identical at every price because
quantity and the lower line price factor out. A 100..102 range with 70 grids
fails; fewer grids can fit. Radar reduces the count without moving the bounds;
if even one interval fails it reports GRID_FEES and withholds the setup.
Externally supplied counts fail admission if their actual spacing fails, even
when their claimed step is larger. MISSING_STRUCTURE means either chart level
is absent. Existing open grids are not resized. This is a fee feasibility check;
funding, initial inventory costs, and exit losses still affect total bot net PnL.


## Confirmed minimum grid return — 11 September 2026

Supersedes target-step/geometric sizing for new entries: Dan confirmed the
KuCoin “Profits per Grid (fees deducted)” basis, strictly greater than 1%,
including the least profitable grid anywhere within the configured range.
Choose the largest arithmetic grid count up to 200 passing that floor without
moving support or resistance. Interval = (high − low) / count, rounded down to
the stored contract tickSize when available. Median pivot boundaries round inward
to that tick (support up, resistance down), never widening the range. Missing tick metadata leaves an
unrounded estimate; displayed returns remain estimates, not exchange quotes.

For a Long/Neutral pair: net = quantity × (sell − buy) − quantity ×
0.0006 × (buy + sell); return% = 100 × net / (quantity × buy / leverage).
Short uses sell-side allocated margin; Neutral applies the more conservative
short-side minimum so both directions clear the floor. Evaluate the conservative top pair
(high − interval, high), including any rounding remainder, for the minimum;
the bottom pair gives the maximum. Both fees are included; funding is accounted
separately. Leverage is 5×. Exactly 1% is rejected. Forecast grids/hour uses
interval / current price rather than a former fixed target step.

RAY reference checks: 1.4..2, 70, tick .0001 yields interval .0085 and roughly
1.53..2.43%; 1.1..2 yields .0128 and roughly 2.61..5.21%. These validate the
percentage formula, not KuCoin's still-unverified per-order quantity allocation.
New paper orders and dashboard ladders use the same arithmetic interval.
Existing bots retain their original orders and are labeled legacy; they are not
silently resized or represented as passing this new entry requirement.
Reference definition: https://www.kucoin.com/support/21959472633113

New entry layout: at least 70 configured arithmetic grids, each full pair with
estimated return strictly above 1% on allocated margin at 5× after both 0.06%
fill fees. Long targets 40% buy / 60% sell orders; Short 60% buy / 40% sell;
Neutral 50% / 50% across its two books, allowing one order of rounding. The
selector tries confirmed support/resistance pairs, choosing the narrowest pair
that passes and then its highest qualifying count. It never invents range
limits. No valid pair means no offer; admission checks the split again using
the current quote. Existing positions keep their original settings.
