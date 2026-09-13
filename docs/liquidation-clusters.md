# Implied liquidation clusters (`oi_delta_implied_v1`)

Dan's decision on 2026-09-13: **derive liquidation clusters from our own open
interest, pay for nothing until the whole system works.** This document is the
model, its assumptions, its limits, and how the rest of the system consumes it.

- Producer: `trader/data/liquidation_clusters.py`
- Consumer: `trader/radar/liquidity_levels.py` (advisory radar annotation)
- Schedule template: `config/launchd/com.danslab.trader-liq-clusters.plist.example`
- Inputs: `open_interest` and `ticker_snapshots` in the Phase-2 market database
- Cost: zero. No provider request, no credential, no network, read-only SQLite.

## The model

Every ~5 minutes the collector stores, per symbol, open interest in contracts,
the mark price, and the funding rate. Between two consecutive snapshots:

1. **ΔOI > 0 — positions were opened.** Notional opened is
   `ΔOI × multiplier × mark_price` USD. Those positions were opened *near the
   current mark price*, which is the one thing an OI series tells us reliably.
2. **Spread across leverage tiers.** Retail perpetual leverage clusters on round
   numbers. Weights `{5: .25, 10: .30, 20: .25, 25: .10, 50: .10}`, restricted to
   tiers at or below the contract's `maxLeverage` and renormalised to one. A
   contract capped below 5× produces no clusters at all rather than a guess.
3. **Split long/short, tilted by funding.** Base 50/50. Positive funding means
   longs are paying shorts, so the crowd is long-heavy: 60/40. Negative funding:
   40/60. Funding sign is the only directional evidence in the data we store.
4. **Convert to liquidation prices.** With `mmr = maintainMargin` from the
   contract and leverage `L`:
   - long liquidation `= P × (1 − 1/L + mmr)`
   - short liquidation `= P × (1 + 1/L − mmr)`
5. **ΔOI < 0 — positions were closed.** The closed notional is removed
   *proportionally from every existing bin*. We do not know which positions
   closed, so we must not pretend to; proportional removal is the only unbiased
   choice. Removal larger than the standing mass empties the book.
6. **Decay.** Every bin decays with a 48-hour half life, applied on elapsed time
   between snapshots and once more from the last snapshot to `now_ms`. Old
   inferred leverage is progressively less likely to still be open.
7. **Binning.** Prices are bucketed at `max(tick_size, 0.25% of price)`, using
   the *current* price so every bin in the file is commensurable.
8. **Window.** The last 168 hours (7 days).

### Output shape

```json
{"schema_version": 1, "method": "oi_delta_implied_v1",
 "generated_at_ms": 1789321389163, "window_hours": 168,
 "symbols": {"AAVEUSDTM": {
   "price": 126.94, "total_usd": 39444.06, "snapshots_used": 764,
   "clusters": [{"price": 115.19805, "side": "long", "usd": 7099.93, "age_h": 0.115}],
   "nearest_below": {"price": 125.35325, "side": "long", "usd": 2366.64, "distance_pct": 1.25},
   "nearest_above": {"price": 128.52675, "side": "short", "usd": 1359.31, "distance_pct": 1.25}}}}
```

- `clusters` — the top 12 bins by USD. Bins holding less than 0.5% of the
  symbol's total mass are dropped as noise.
- `age_h` — USD-weighted mean age of the mass in that bin.
- `distance_pct` — always a positive percentage away from the current price.
- `nearest_below` / `nearest_above` — the closest surviving bin on each side, or
  `null` when price has cleared every cluster on that side.
- Every number is finite; the file is written with `allow_nan=False`.
- A symbol with no usable contract terms or fewer than two snapshots is simply
  absent from `symbols` rather than present with fabricated zeros.

## What this is NOT

- **Not a heatmap of resting orders.** No order book is involved. Nothing here
  is a price anyone has committed to trade at.
- **Not ground truth.** It is an inference chain: OI change → assumed entry
  price → assumed leverage mix → assumed side mix → liquidation arithmetic.
  Every step after the first is an assumption.
- **Not a position-level ledger.** We never know *whose* OI changed, at what
  leverage, or which positions closed. Bins are aggregate mass, not accounts.
- **Not cross-exchange.** This is KuCoin perpetual OI only. The paid CoinGlass
  heatmap aggregates venues; this does not, and will read differently.
- **Not an admission signal.** It never gates a setup, changes `rank_score`,
  moves a range, or reorders a section.

Known biases: net OI hides simultaneous opens and closes, so a flat OI period
with heavy churn registers as nothing; the 5-minute cadence blurs the entry
price of anything opened between ticks; the funding tilt is weak evidence; and
early in the database's life the window is shorter than 168 hours (the
Phase-2 database starts 2026-09-10, so `snapshots_used` is currently ~760, not
the ~2016 a full week would give).

## How the radar uses it

`python -m trader.radar --liquidation-clusters PATH` is optional and additive.
When the file exists, carries `schema_version: 1` and is **younger than three
hours**, each radar row gains four numeric fields:

| Field | Meaning |
|---|---|
| `liq_below_pct` | distance to the nearest cluster below price, percent |
| `liq_below_usd` | inferred USD mass at that cluster |
| `liq_above_pct` | distance to the nearest cluster above price, percent |
| `liq_above_usd` | inferred USD mass at that cluster |

and the report gains a top-level `liq_clusters_generated_at_ms`. Unknown values
annotate `0.0`, never `null`. A missing, unreadable, wrong-schema, future-dated
or stale file annotates zeros and the radar run still succeeds — the annotation
can never take the radar down. Without the flag the radar output is unchanged,
field for field.

The LaunchAgent template runs at minute 3 of every hour, two minutes before the
`:05` radar, so the radar always reads a file well inside the three-hour
freshness bound. It is an `.example`; installing it is Dan's call.

## How a later hedge trigger will consume it (T5)

T5 opens an offsetting position instead of closing a losing bot. The two
fields that matter are the ones on the side the position is exposed to:

- A long-inventory bot reads `liq_below_pct` / `liq_below_usd`. A large mass a
  short distance below price is a cascade risk *in the direction that hurts*:
  hedge earlier, and size the hedge against `liq_below_usd` relative to the
  symbol's `total_usd`.
- A short-inventory bot reads `liq_above_*` symmetrically.
- When the nearest cluster is far away or thin, the trigger falls back to the
  pure inventory-loss-versus-grid-profit threshold, unchanged.

Because the fields are always present and always finite, the trigger needs no
null handling: zeros mean "no evidence", which must map to "no adjustment",
never to "maximum urgency".

## Validating against executed liquidations

We already store real executed liquidation USD per hour in
`coinglass_liquidations` (aggregated Binance/OKX/Bybit, nine symbols, written
hourly by `trader/data/coinglass_history.py`). That is the honest check on this
model, and it is a *correlation* check, not an equality check — the venues and
the definition differ.

The procedure, for the last 48 hours and for each symbol present in both:

1. For each completed hour `h`, rebuild the clusters **as of the start of `h`**
   (`build(..., now_ms=h)`), so nothing after `h` leaks into the prediction.
2. Take that hour's 1h candle from `klines` and sum the cluster USD whose price
   falls inside `[low, high]` — the mass price actually swept. Long clusters
   count when price fell into them, short clusters when price rose into them.
3. Pair that predicted swept USD with `long_usd + short_usd` from
   `coinglass_liquidations` for the same hour.
4. Report the Pearson correlation over the ~48 pairs per symbol, plus the
   pooled correlation on rank-normalised values (levels differ by orders of
   magnitude between venues; ranks are the comparable part).

A positive, stable correlation means the *shape* of the inference is right even
though the magnitude is ours alone. A correlation near zero means the leverage
mix or the funding tilt is wrong and should be re-fit before T5 leans on it.

**Not implemented.** `--validate` is deliberately absent from this task: an
honest version needs a 48-way rebuild per symbol plus candle joins and a
statistics layer, which is a larger piece of work than the derivation itself
and would have pushed this module past its size budget. The procedure above is
the specification for it; the data it needs is already being collected hourly,
so the check can be run later against history without losing anything now.

## Running it

```sh
python3 -m trader.data.liquidation_clusters \
  --database ~/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3 \
  --from-radar ~/Sandbox/grokbot/radar/radar.json --limit 60 \
  --out ~/Sandbox/grokbot/market-data/liquidation-clusters.json
```

`--symbols A,B` replaces `--from-radar`. The file is written atomically (write
to a dot-prefixed sibling, `chmod 0644`, rename), so a reader never sees a
partial file. One JSON status line goes to stdout for the doctor to read:

```json
{"generated_at_ms": 1789321389163, "status": "pass", "symbols_ok": 60, "symbols_requested": 60}
```

`status` is `pass` when every requested symbol produced an entry, `warn` when
some did, `fail` (exit 1) when none did.

_Last verified: 2026-09-13_
