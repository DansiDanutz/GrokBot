# Local market-data storage

`Store(path)` opens a local SQLite database in WAL mode with a 5-second busy
timeout. No provider calls, runtime activation or exchange credentials occur.

```python
from trader.data.store import Store

with Store("/absolute/private/directory/market.sqlite3") as store:
    with store.transaction():
        store.upsert("klines", candle_rows)
        store.upsert("checkpoints", checkpoint_rows)
    candles = store.query(
        "SELECT * FROM klines WHERE symbol=? AND interval=? ORDER BY time_ms",
        ("XBTUSDTM", "1m"),
    )
```

`upsert(table, rows)` accepts dictionaries with **exactly** the documented
columns and returns the number of input records applied. Duplicate primary keys
update the existing row. Values never appear in generated SQL. Validation and
SQLite failures roll back the entire batch. `transaction()` groups several
upserts in one atomic savepoint, including a candle page plus its checkpoint.
Owned transactions reserve the SQLite writer with `BEGIN IMMEDIATE` before
reading, preventing snapshot-upgrade races between backfill and collector
processes. Nested failures preserve outer work when caught. The exposed `connection` is
for explicit caller-controlled SQLite transactions; upserts never commit them.
A Store connection belongs to its creating thread. Callers open separate
connections for separate threads/processes; SQLite serializes writers.

## Schema version 1

Every field is required in input rows. `nullable` fields use explicit `None`.
All timestamps are integer UTC **milliseconds**. Candle `time_ms` identifies
its interval's opening boundary; CoinGlass `time_ms` is the hourly bucket's
opening boundary. Provider adapters must convert units before storage.

| Table | Primary key | Additional columns |
|---|---|---|
| `klines` | `symbol, interval, time_ms` | `open, high, low, close, volume, turnover` |
| `funding` | `symbol, time_ms` | `rate, period_ms` (nullable) |
| `open_interest` | `symbol, time_ms` | `observed_at_ms, source_time_ms` (nullable), `open_interest` |
| `top_of_book` | `symbol, time_ms` | `observed_at_ms, bid, ask, bid_size, ask_size` |
| `coinglass_liquidations` | `symbol, exchange, time_ms` | `long_usd, short_usd` |
| `ticker_snapshots` | `symbol, time_ms` | `observed_at_ms, source_time_ms` (nullable), `last, mark_price, index_price, volume_24h, turnover_24h, open_interest, funding_rate, raw_json` |
| `checkpoints` | `source, symbol, interval` | `next_time_ms, updated_at_ms` |
| `universe` | `symbol` | `updated_at_ms, first_candle_ms` (nullable), `listed_at_ms` (nullable), `turnover_30d` (nullable), `atr_pct` (nullable), `multiplier, lot_size, active, coverage_json` |
| `data_quality` | `check_name, symbol, interval, start_ms, end_ms` | `checked_at_ms, status, details_json` |

- Candle and checkpoint intervals are `1m` or `1h`. Data-quality intervals also
  allow `5m` for collector-cycle status. Status is `pass`, `warn`, `fail` or
  `unknown`; `active` is integer 0 or 1.
- Prices must be positive and finite; OHLC ranges must be consistent. Volumes,
  sizes, turnover, OI and liquidation amounts must be finite and nonnegative.
  Funding rates may be negative. Funding `time_ms` is the source funding event
  timestamp; do not invent its historical period. Missing period is `None`.
- Turnover is quote-currency notional. Volume and OI remain the provider's native
  contract/base units; adapters document units and do not mix incompatible
  contracts under a symbol. CoinGlass amounts are USD, `exchange` identifies the
  precise source exchange or aggregate exchange list (not KuCoin provenance).
- Book `time_ms` is the actual venue event time; `observed_at_ms` is the collector
  clock. OI and contract ticker snapshots use the collector observation time as
  `time_ms` when no venue timestamp exists. Preserve a known venue timestamp in
  `source_time_ms`; otherwise store `None`, never invent freshness.
- `raw_json`, `coverage_json` and `details_json` must be JSON object text with no
  nonfinite numbers. Ticker raw JSON retains the full public provider snapshot.
  Keep credentials and private account responses out of these payloads.
- `first_candle_ms` means the first **observed stored candle**, not confirmed
  listing inception. `listed_at_ms` requires independently supplied listing
  evidence. Missing ages/statistics stay `None`; `coverage_json` documents
  observation bounds, samples and gaps so partial data is not presented as a
  complete 30-day history.

## File and migration guarantees

The database path rejects symlinks in its ancestors, leaf or SQLite sidecars,
non-regular or multiply linked database files, foreign ownership, and parent
traversal. Ownership/link count are checked before changing permissions.
New directories are private (0700), the
DB is 0600, and SQLite creates sidecars using the database permissions. Supply a
canonical path if a system alias such as `/tmp` is a symlink. Keep the database
inside a private directory: path checks cannot secure attacker-writable parent
directories against concurrent replacement. No configured runtime directory is
created automatically; only the explicitly supplied path is used.

Schema SQL and `PRAGMA user_version` commit in a single migration transaction.
A failed migration leaves no partial schema. Reopening version 1 is idempotent;
unknown newer versions, incompatible table/primary-key layouts and unversioned
nonempty databases fail closed. A failed open can leave a new empty database
file, but never fabricates or resets stored records. The current migration is
0 → 1; future migrations must be explicit and versioned.
