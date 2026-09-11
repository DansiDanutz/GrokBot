# Phase 2.4 — Offline contract registry

The registry records the current validated crypto USDT perpetual universe and
statistics from the SQLite store. It does not make provider requests, create
orders or change a running service. It imports only pure validation helpers from
the phase 2.2 public-client module. CLI database paths are always explicit.

```sh
python3 -m trader.data.universe --database /absolute/path/market.sqlite
python3 -m trader.data.universe --database /absolute/path/market.sqlite --refresh
```

`--refresh` reads direct full-contract metadata in `ticker_snapshots.raw_json`
from the latest `observed_at_ms` epoch at or before the chosen time. It neither
mixes older symbols from other epochs nor includes future snapshots. The CLI
cannot establish response completeness; absent registry symbols remain as they
were. `--now-ms` supplies a reproducible UTC millisecond cutoff. A missing
snapshot fails explicitly without resetting existing records. The output gives
registry rows, parsed coverage objects, age fields and aggregate coverage counts.
The last metadata observation time remains distinct from metric refresh time.

Programmatic integration uses:

```python
from trader.data.universe import refresh_registry

report = refresh_registry(
    store, contracts=validated_raw_contract_rows, now_ms=as_of_ms, complete=True
)
```

Callers may set `complete=True` only after a successful, complete active-contract
response, with the entire selected universe. A truncated/filtered response or a
partially failed updater cycle is not complete. Supplied metadata goes through
`select_contracts` again; contract quantities, listing dates and lot integrality
are checked before atomic persistence. Complete empty responses require review.
The module does not claim a retrieval timestamp for supplied metadata because
raw contract fields do not carry one. The caller owns source-retrieval evidence.

## Dates and age bounds

- `listed_at_ms` is the provider-reported `firstOpenDate`, not a date inferred
  from available candles. The public validator requires that field. Missing or
  malformed listing metadata rejects the refresh rather than inventing a date.
  The storage/report schema nevertheless permits null dates for unknown records.
- `first_candle_ms` is the oldest **observed completed** stored candle across
  1-minute and 1-hour bars. It is null when no completed candle is stored. It is
  never described as the actual first-ever exchange candle.
- `listing_age_ms` uses the reported listing date. `observed_history_age_ms`
  measures available history. Unknown/future dates yield null age, not zero.
  The listing-age lower-bound flag is null when either date is unavailable, and
  false if stored history precedes the reported listing date. Such disagreement
  needs source review rather than an invented listing history.
- A stale refresh cannot overwrite or deactivate a registry updated later.

## Turnover and ATR

`turnover_30d` is the sum of stored **completed 1-minute quote-currency turnover**
inside `[floor(now / minute) - 30 days, floor(now / minute))`. Hourly bars are
excluded to avoid double counting. Coverage discloses observed/expected candle
counts (43,200 expected), the fraction, window and observed endpoints. An empty
window returns null. A real observed zero returns zero. A partial sum remains
explicitly partial and is not a complete 30-day total, median or forecast.
New listings normally have partial 30-day coverage; pre-listing candles are not
invented or padded with zero volume.

`atr_pct` uses the arithmetic mean of **14 true ranges** from exactly the latest
15 consecutive completed 1-hour bars, normalized by the last close and multiplied
by 100. True range is `max(high-low, abs(high-prev_close), abs(low-prev_close))`.
This is the initial/simple ATR definition, not a perpetually Wilder-smoothed
series. Missing, gapped or stale hourly history returns null with coverage, and
a forming hour never substitutes for the last completed hour.

## Survivorship and limits

After an explicitly complete refresh, missing former members are retained with
`active=0`, their dates and last metric coverage preserved, and an absence reason.
This is evidence of absence from the latest active universe, not proof of an
exact delisting timestamp. Partial refreshes never deactivate missing names.
Reappearing contracts become active again. The registry is a current-state table,
not a historical membership timeline. Starting with today's active contracts
still introduces survivorship bias in historical research; retained inactive
records reduce future loss of evidence but cannot reconstruct already missing
markets. Point-in-time universes require dated source snapshots and additional
replay logic before backtests can make survivorship-free claims.

## Offline verification

The task was built in an isolated temporary source copy. Initial RED evidence is
`/tmp/grok-phase2-24-red.log`; additional edge-case RED evidence is
`/tmp/grok-phase2-24-edge-red.log`. Registry and combined-store GREEN logs are
`/tmp/grok-phase2-24-green.log` and `/tmp/grok-phase2-24-combined-green.log`.

```sh
python3 -m unittest trader.tests.test_universe -v
python3 -m unittest discover -s trader/tests -v
```

The registry suite covers 110 synthetic contracts with reported listing ages,
observed history, full/partial/absent turnover, completed-hour ATR continuity,
inactive retention, reactivation, stale updates, atomic failure, source epochs,
raw-symbol mismatch and network-free CLI operation. No real provider universe
size, backfill completeness or live freshness is claimed by those fixtures.
