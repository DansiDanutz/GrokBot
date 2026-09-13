# T5 hedge trigger — backtest first, build second

Task T5 of `docs/GROKBOT-PLAN.md`. Dan's recorded decision on 2026-09-13 for a
losing bot with working grids: **hedge first; close only if a better coin is
free.** The opportunity-cost close is T6 and is built separately.

- Backtest: `trader/review/backtest_hedge.py` (read-only, offline)
- Trigger: `trader/autopilot/hedge.py` — `should_hedge`, `hedge_leg`
- Constants: `trader/autopilot/constants.py` — `HEDGE_*`
- Wiring: `trader/autopilot/policy.py` — `_hedge_gate`, called in `decide()`
- **Status: shipped dark.** `HEDGE_ENABLED = False`. Nothing changes until Dan
  flips it, or sets `hedge_enabled: true` in the learned-rules store.

## Why this exists

Across the 23 closed paper bots the grid ledger earned **+593.13 USDT** and the
directional inventory those grids accumulate cost **−760.62** (realised +
unrealised − grid ledger − fees − funding; the backtest reproduces both numbers
exactly). Eleven of the 23 exited on `RANGE_BREAK` — the moment a grid bot
holds its most adverse inventory and price is leaving the range. The grid
machine works; what it carries is what bleeds.

## What the hedge does

`hedge_leg(bot, price, now_ms)` turns a one-book directional bot into a
two-book bot by adding an offsetting leg sized to the **exact current net
position**, so the net is flat at the mark. The original ladder keeps running
and keeps harvesting grids; the offsetting leg is static. Everything else is
the existing `hedge_books` machinery from `trader/papergrid/neutral.py`, so
accounting (`_sync`), funding (`_fund` per book), the liquidation guard
(`risk.protection_needed` already iterates `bot.get('hedge_books', [bot])`) and
the flatten (`close_neutral`) all keep working unchanged.

At the hedge instant the bot's equity falls by exactly one taker entry fee and
by nothing else. There is no free money in this.

**Why the offsetting leg carries no ladder of its own.** A mirror grid book
does not hedge. A grid book's position is `q·(grids − e(p))` where `e(p)` is
the empty line at price `p`. Seed an opposite ladder flat at `e0` and the
combined position works out to `2q·(e0 − e(p))` — it *doubles* the exposure to
every further adverse line instead of removing it. A static leg leaves the net
delta at `P(p) − P(p_hedge)`, which is what "flat at the mark" means. Grid
counting still spans both books: `_sync` sums `completed_grids` and
`grid_profit` over `hedge_books`, so a hedged bot keeps counting grids.

**Un-hedging is out of scope.** Once hedged, the bot keeps harvesting grids and
leaves through the normal close path (`RANGE_BREAK`, `LABEL_FLIP`, `MAX_AGE`,
`RISK_LIMIT`, …), which flattens both books with one fill each. There is no
path back to a single book.

**NEUTRAL bots are not eligible.** They already carry two books; `_sync`
unpacks exactly two positionally, so there is no third book to add.

## Method

For each of the 23 closed bots the backtest rebuilds the bot from its own
stored specification (`range_low/high`, `grids`, `grid_interval`, `leverage`,
`notional`, `direction`, `opening_price`, `funding_pct`, stored fee rates),
replays `opened_ms` → `closed_ms` through 1m klines from the Phase-2 market
database, and at every 15-minute mark records grid profit, inventory P&L,
position fraction and the point-in-time implied liquidation clusters (derived
from `oi_delta_implied_v1` over the open interest observed strictly before that
mark, so no rule can see the future).

Rule family: hedge when **inventory loss > K × grid profit so far** AND
**|position| ≥ F × full-range position** AND (optionally) **price is within D%
of the nearest implied cluster on the losing side**. Sweep: K ∈ {1.0, 1.5, 2.0},
F ∈ {0.5, 0.75}, D ∈ {none, 2%, 5%}.

Where a rule fires, the bot is hedged at that mark and the replay continues to
the real close time and closes for the real reason. **The reported delta is
hedged net minus that same replay's own unhedged net**, so replay error cancels
out of every delta. The "live net" column is shown only so the replay's
faithfulness can be judged.

Reproduce:

```
python3 -m trader.review.backtest_hedge \
  --state   ~/Sandbox/grokbot/autopilot/state.json \
  --database ~/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3
```

## Result (2026-09-13, 23 closed bots)

```
bot    symbol       direction reason           candles     grids  replay net    live net
------------------------------------------------------------------------------------------
1      AKEUSDTM     NEUTRAL   PROFILE_UPDATE        69    5/6          -0.49       -0.07
2      RAYUSDTM     NEUTRAL   LABEL_FLIP             6    0/0          -0.04       -0.05
3      NESUSDTM     LONG      PROFILE_UPDATE        47    5/5          -2.91       -9.04
4      NEARUSDTM    LONG      PROFILE_UPDATE        47    1/2         -12.33       -9.21
5      ENAUSDTM     SHORT     PROFILE_UPDATE        47    3/3         +20.18      +19.24
6      RAYUSDTM     NEUTRAL   PROFILE_UPDATE       104   20/17         +1.73       +1.27
7      NESUSDTM     LONG      PROFILE_UPDATE        74    2/2          +1.94       +7.58
8      NEARUSDTM    LONG      PROFILE_UPDATE        74    0/0        -111.45      -97.07
9      ENAUSDTM     SHORT     PROFILE_UPDATE        74    3/3         +62.23      +63.15
10     RAVEUSDTM    NEUTRAL   LABEL_FLIP            70   20/18         -2.26       -4.42
11     NESUSDTM     LONG      RANGE_BREAK          424   76/61        +45.91      +43.37
12     PEPEUSDTM    SHORT     RANGE_BREAK          505   12/11        -57.06      -50.57
13     NEARUSDTM    LONG      RANGE_BREAK            0    0/0          -3.00      -34.66
15     4USDTM       LONG      RANGE_BREAK          181  109/81       +143.33     +135.37
16     FHEUSDTM     SHORT     RANGE_BREAK           74    3/1        -110.28     -102.61
17     NEARUSDTM    NEUTRAL   RANGE_BREAK         1924  183/160       +13.71       +9.87
18     JUPUSDTM     LONG      RANGE_BREAK         1242   32/31        -65.82      -67.92
19     MYXUSDTM     SHORT     RANGE_BREAK         1297   39/32        +74.45      +73.61
20     RAYUSDTM     LONG      RANGE_BREAK          581   53/42       -155.25     -156.00
21     LABUSDTM     NEUTRAL   RANGE_BREAK           44   48/38         -4.34      -10.84
22     ALLOUSDTM    NEUTRAL   LABEL_FLIP           431   19/15         -3.76       -4.33
23     CAKEUSDTM    LONG      PROFILE_UPDATE       232    3/2         -16.10      -17.35
25     NIULAIUSDTM  LONG      RANGE_BREAK           40   16/15        +44.12      +43.18

no-op baseline: 23 bots, replayed net -137.48 USDT (live ledger net -167.49)

rule                   triggered     total d      mean d    median d     worst d  improved
------------------------------------------------------------------------------------------
K=2.0 F=0.50 D=none           11      +40.11       +3.65      +10.98     -257.06     7/11 
K=2.0 F=0.50 D=5%             11      +40.11       +3.65      +10.98     -257.06     7/11 
K=1.0 F=0.50 D=none           11      +38.29       +3.48      +10.98     -257.06     7/11 
K=1.0 F=0.50 D=5%             11      +38.29       +3.48      +10.98     -257.06     7/11 
K=1.5 F=0.50 D=none           11      +38.29       +3.48      +10.98     -257.06     7/11 
K=1.5 F=0.50 D=5%             11      +38.29       +3.48      +10.98     -257.06     7/11 
K=1.5 F=0.50 D=2%              9      -61.69       -6.85      +10.98     -257.06     5/9  
K=1.0 F=0.50 D=2%              9      -61.86       -6.87      +10.98     -257.06     5/9  
K=1.0 F=0.75 D=none            6      -93.66      -15.61      +41.13     -369.16     5/6  
K=1.0 F=0.75 D=5%              6      -93.66      -15.61      +41.13     -369.16     5/6  
K=1.5 F=0.75 D=none            6      -93.66      -15.61      +41.13     -369.16     5/6  
K=1.5 F=0.75 D=5%              6      -93.66      -15.61      +41.13     -369.16     5/6  
K=2.0 F=0.50 D=2%              7     -111.88      -15.98       -5.10     -257.06     3/7  
K=2.0 F=0.75 D=none            6     -118.06      -19.68      +41.13     -369.16     5/6  
K=2.0 F=0.75 D=5%              6     -118.06      -19.68      +41.13     -369.16     5/6  
K=1.0 F=0.75 D=2%              5     -181.24      -36.25      +14.57     -369.16     4/5  
K=1.5 F=0.75 D=2%              5     -181.24      -36.25      +14.57     -369.16     4/5  
K=2.0 F=0.75 D=2%              3     -255.82      -85.27       +8.15     -369.16     2/3  

best rule by total delta: K=2.0 F=0.50 D=none -> +40.11 USDT over 11 bots

per-bot outcome of K=2.0 F=0.50 D=none

bot    symbol       direction close reason   hedge min  grid then   inv then   unhedged    hedged   delta
----------------------------------------------------------------------------------------------------------
15     4USDTM       LONG      RANGE_BREAK           18      +5.28     -13.40    +143.33   -113.73 -257.06
25     NIULAIUSDTM  LONG      RANGE_BREAK            8      +1.30      -7.88     +44.12    -40.66  -84.78
7      NESUSDTM     LONG      PROFILE_UPDATE         9      +0.00     -11.71      +1.94    -15.26  -17.20
3      NESUSDTM     LONG      PROFILE_UPDATE        26      +0.98     -16.82      -2.91    -18.07  -15.16
4      NEARUSDTM    LONG      PROFILE_UPDATE        11      +0.00      -1.05     -12.33     -2.52   +9.80
23     CAKEUSDTM    LONG      PROFILE_UPDATE         6      +0.00      -0.50     -16.10     -5.12  +10.98
12     PEPEUSDTM    SHORT     RANGE_BREAK           25      +0.00      -8.46     -57.06    -16.30  +40.76
18     JUPUSDTM     LONG      RANGE_BREAK           53      +1.15     -12.06     -65.82     -5.09  +60.74
8      NEARUSDTM    LONG      PROFILE_UPDATE         9      +0.00     -29.30    -111.45    -41.85  +69.60
16     FHEUSDTM     SHORT     RANGE_BREAK            9      +0.00      -2.56    -110.28    -32.63  +77.65
20     RAYUSDTM     LONG      RANGE_BREAK           12      +1.02      -4.37    -155.25    -10.47 +144.78

total +40.11 USDT; drop the single best bot -> -104.67; drop the single worst -> +297.16; positive on 7 of 11 bots
```

## Verdict: the hedge helps, but the evidence is weak

The best rule in the family is **K=2.0, F=0.50, D=none**: it fires on 11 of the
23 bots, improves 7 of them, has a **positive median of +10.98 USDT**, and
turns a −137.48 replayed baseline into −97.37 — a **+40.11 USDT** total edge.

That edge is not robust and should not be presented as one:

- **Drop the single best bot (RAY, +144.78) and the total becomes −104.67.**
  Drop the single worst (4USDTM, −257.06) and it becomes +297.16. One bot
  decides the sign of the answer. That is the whole finding.
- The inventory-ratio gate K is effectively **non-binding**: K=1.0, K=1.5 and
  K=2.0 all fire on the same 11 bots and differ only in when one of them
  (JUP) triggers. At F=0.50 the position-fraction gate is doing all the work.
  K=2.0 is shipped because it is the most conservative of three equals.
- **The liquidation-cluster gate did not earn its place.** D=5% is identical to
  D=none in every configuration — every trigger was already within 5% of a
  cluster on its losing side, so the gate never binds. D=2% is strictly worse
  everywhere (it prunes winners: at F=0.50 it drops the total from +38 to −62).
  `HEDGE_CLUSTER_DISTANCE_PCT` therefore ships as `None`. The T4 cluster feed is
  wired in and tested, but it is not currently part of the decision.
- Raising F to 0.75 is clearly worse (−94 to −118), so hedging later is not
  the answer either.
- The mechanism is insurance, and it behaves like insurance. It wins big where
  price kept going (RAY +144.78, FHE +77.65, NEAR +69.60, JUP +60.74) and loses
  big where price came back (4USDTM −257.06, NIULAI −84.78). Four of the eleven
  triggers were on bots that ended up **profitable unhedged**; two of those are
  the two largest losses in the table.

## Sample-size and method caveats — read these before enabling anything

1. **23 bots, 11 triggers.** That is an anecdote with a standard error far
   larger than the effect. No significance is claimed and none should be read.
2. **One market regime.** Every bot lived between 2026-09-11 17:33 and
   2026-09-13. Two days, one tape, heavily overlapping symbols (NEAR and NES
   appear three times each, RAY three times). These are not 23 independent
   observations.
3. **1m-snapshot fills.** The live desk ticks every 10s; the replay walks 1m
   OHLC with the engine's open→extreme→extreme→close path. The replay
   consistently books **more** grids than the live bot did (e.g. 4USDTM
   109 vs 81, NEAR-17 183 vs 160). Deltas cancel most of this, but a hedged
   and an unhedged branch do not traverse an identical fill sequence forever.
4. **Funding is modelled, not replayed.** Live bots are `funding_managed`; the
   runtime charges them from stored settlement evidence the replay cannot
   reach. The replay charges the engine's own 8-hourly schedule at the bot's
   stored `funding_pct` on **both** branches.
5. **Nine pre-v2 bots have no stored `opening_price`.** For those the first 1m
   open inside the bot's life is used, clamped into the bot's range.
6. **Clusters are an inference, not an observation.** See
   `docs/liquidation-clusters.md`. Open-interest snapshots begin 2026-09-11
   01:38, before the first bot opened, so — unlike the original plan assumed —
   every bot did get a real point-in-time cluster view and none needed a forced
   D=none. The gate still failed to help.
7. **One closed bot (13, NEAR) has a zero-minute life** and no klines at all; it
   contributes a baseline and never a trigger.

## Safety

- `constants.HEDGE_ENABLED = False`. With the flag off, `_hedge_gate` returns
  before touching anything and `decide()` is byte-identical. The full
  pre-existing suite at `design/polish` 7a4534f (38 node + 351 paper + 635
  trader + 24 team) stays green; this task adds 22 trader tests, for 657.
- The learned-rule override `hedge_enabled` is **absent from `EMPTY_RULES`**, so
  every existing store stays valid. `validate_store` accepts only `true`,
  `false` or absent; anything else invalidates the store, and an invalid store
  fails closed to no rules at all.
- Missing, zero or stale cluster annotations fail the cluster gate **closed**,
  never open. (Moot while D is `None`, but it is the behaviour when D is set.)
- Paper only. `hedge_leg` writes to a ledger dict. No exchange order exists on
  any path.
- Turning it on is Dan's act, not ours.

## How to enable it (when Dan decides to)

Either set `HEDGE_ENABLED = True` in `trader/autopilot/constants.py`, or add
`"hedge_enabled": true` to the `rules` object of
`~/Sandbox/grokbot/autopilot/learned-rules.json`. The override wins over the
constant in both directions, so `false` in the store also switches it back off
without a code change. Re-run the backtest first — it takes four seconds.

## Events

- `HEDGE` — `ts_ms`, `type`, `bot_id`, `symbol`, `price`,
  `position_contracts`, `grid_profit`, `inventory_pnl`. Numeric fields only.
- `DECISION` with `action="hedge"` and `rule_blocks=[20]`
  (`DECISION_RULES['hedge_trigger']`), carrying the usual decision context.

_Last verified: 2026-09-13_
