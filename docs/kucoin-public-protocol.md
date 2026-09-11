# KuCoin public futures data protocol — 2026-09-11

Scope: anonymous GET only. Eight bounded requests were sent, spaced by two seconds; no authentication, keys, accounts, orders, or paid providers. Responses are public market snapshots, not trading recommendations. Contracts fixture intentionally contains only XBTUSDTM and ETHUSDTM; original_count preserves observed universe size.

## Classic endpoints (use one consistent protocol)

Base `https://api-futures.kucoin.com`.

| Path | Query | Weight | Meaning |
| --- | --- | --- | --- |
| `/api/v1/contracts/active` | none | 3 | Currently tradable contracts, metadata, current OI and 24h turnover |
| `/api/v1/kline/query` | symbol, granularity=1 or60, from, to | 3 | OHLCV buckets; granularity minutes; bounds milliseconds; maximum500 rows |
| `/api/v1/contract/funding-rates` | symbol, from, to | 5 | Historical settlement rates; bounds and timepoint milliseconds |
| `/api/v1/ticker` | symbol | 2 | Latest trade and best bid/ask; ts nanoseconds |
| `/api/v1/level2/depth20` | symbol | 5 | Aggregated20-level snapshot; also depth100; ts nanoseconds |

All five are documented Public pool. Public funding docs say permission Futures, but anonymous request succeeded; do not add authentication. Book/ticker sizes and openInterest are contract quantities, not USDT. Use linear contract multiplier and price when deriving base/notional units; preserve raw values too.

Candle rows: `[open_time_ms, open, high, low, close, volume_lots, turnover_usdt]`. Both volume columns count one side of trades. Do not confuse with spot candle ordering or UTA v2: v2 has a different shape, second timestamps and a200-row limit.

## Pagination and availability

For an internal half-open interval `[start,end)`, request `from=start`, `to=end-1`. Advance by a fixed window no longer than 200 candle slots, independent of returned count. Validate/sort/deduplicate rows by opening timestamp and reject conflicts. Never fabricate zero-volume bars to make a gap look complete. Missing no-tick intervals are explicitly allowed by provider docs. Exclude unfinished current candles using floor(now / interval).

Two live requests at90days ago established inclusive provider bounds: `from=T,to=T+120000` yielded3 one-minute rows; `to=T+119999` yielded2. A90-day-old one-hour request yielded2 rows. These demonstrate sampled availability, not every-symbol or full90-day completeness.

The classic candle docs state no total-history guarantee. Current UTA docs specify sub8h data only after2025-01-01, but that is not verified as a classic endpoint constraint. Do not transplant it silently. Contract `firstOpenDate` is millisecond listing/open metadata, not evidence that all intervening candles exist. For a requested90-day range, start at max(range_start, firstOpenDate), persist oldest observed candle plus explicit leading gap/absence status. When listing metadata is unavailable or a leading window is empty, progress bounded chronological windows instead of assuming every empty response means prelisting. An empty sparse window is not a monotonic binary-search predicate. No dedicated first-available-candle endpoint found.

`contracts/active` universe is current and creates survivor bias for historical analysis; persist discovery snapshots and don't claim delisted coverage. Select status Open, quote/settle USDT, noninverse, perpetual expiry metadata (expireDate/settleDate absent/null), and type FFWCSX as documented/observed. Current contracts additionally carry assetClass/marketType; apply CRYPTO if crypto-only universe is intended. Keep unknown shapes rejected/reported rather than guessed.

Funding docs expose no cursor, page-size or maximum time-span parameter. Daily time slices keep requests bounded; sort/deduplicate timepoint, allow changing settlement intervals and retain missing evidence. An end-to-end90-day funding completeness claim needs observed coverage, not an assumed fixed8h schedule. No classic historical OI endpoint verified; store current `openInterest` as forward snapshots only. Bid/ask depth similarly has no historical backfill here.

## Rate limiting

Public pool is IP based. Official page table says2000/30s, though neighboring prose says1-second resets. Live headers show limit2000, remaining decrement matching endpoint weights, reset20247ms→8399ms across requests. Honor `gw-ratelimit-remaining` and `gw-ratelimit-reset` (milliseconds), plus429/Retry-After. Use a conservative shared weighted limiter across collector/backfill, bounded retries/backoff, and avoid retries that spin on repeated failures. These probes used one request each≥2seconds.

## Sources

- https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-klines
- https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-symbols
- https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-ticker
- https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-part-orderbook
- https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history
- https://www.kucoin.com/docs-new/rate-limit-rule-classic
- https://www.kucoin.com/docs-new/v2/rest/ua/get-klines (different protocol; not used)

Fixtures include public URLs/retrieval timestamps; first6 also contain provider rate/date headers. No private headers persisted. Documentation text files were fetched through agent-reach's Jina Reader route. Source webpage changes require protocol revalidation.

## Implemented collector interface

`python3 -m trader.data.kucoin_backfill --database /absolute/private/path/market.sqlite --days 90 --end-ms FIXED_UTC_MILLISECONDS`

The database path is explicit and subject to Store path checks. Default symbols
are the currently active crypto USDT perpetual universe. `--symbols XBTUSDTM
ETHUSDTM SOLUSDTM` narrows discovery; missing requested contracts are errors.
`--max-pages N` permits a bounded smoke run and returns exit status2 while
incomplete. Save the fixed `--end-ms` value for resumption: checkpoint identity
includes the requested range, so extending history cannot silently skip an earlier
unrequested interval. The default end is the latest completed UTC hour. Each page
and its checkpoint commit atomically. Conflicting existing complete candles fail
without replacing evidence. `complete` means all windows were queried; `gap_free`
means all expected bars were present in every requested nonempty series. A
series with no completed bars is marked `no_completed_bars` and cannot supply
gap-free evidence. Future or empty effective windows are rejected.

The default process-shared limiter is 15 weight/second, capacity 15. The incremental
collector may pass a separate configured bucket; coordinate rates across processes
sharing the same egress IP. Provider backpressure is applied under a thread lock.
Transport limits: fixed allowlisted GET paths, no redirects, at most 2 MB decoded
JSON input, 20-second per-attempt socket timeout, three attempts for 429/transient
failures, and an optional absolute monotonic deadline bounding waits and new
requests. The HTTP stream uses single-raw-read chunks where available, checking
the deadline before and after each chunk and after decoding; late results are
discarded. An in-flight socket operation can overrun the deadline by its existing
socket timeout; this is not kernel-level cancellation. Error bodies are never read.
No raw response bodies or exception text are printed in CLI failure output.

## Observed page limit correction

A later 2026-09-11 public probe requested 500 XBTUSDTM minute slots and
received 200 rows. A 200-slot request returned 198 rows (two actual missing
intervals). The implementation therefore caps both backfill and updater pages
at 200 slots, below the documented maximum. Checkpoint identity includes this
page bound so the earlier oversized-page experiment cannot skip repair windows.
The earlier rows remain intact and are compared on refetch. Provider omissions
are still gaps; smaller pages do not justify manufacturing absent candles.

## Bounded parallel backfill

The CLI defaults to `--workers 4` (range 1–4); the Python `run` API defaults to
one worker. Workers share the same public client and 15-weight/second limiter.
Each contract uses a separate thread-owned SQLite connection. Network requests
can overlap; short page/checkpoint writes are serialized within this process to
avoid competing deferred SQLite write transactions. Requests, page size and
checkpoint identity otherwise remain unchanged.

`--max-pages` automatically uses one worker so the bound applies to the entire
run. Reports state the effective worker count. Final contract/interval order is
deterministic. Parallel worker failures appear immediately in progress and in
`failures` plus failed series in the final report; successfully committed pages
and their checkpoints remain available for resume. Progress counters are scoped
to the named contract; final totals aggregate all workers. A failed run is never
reported complete or gap-free.

Before each page request and again before committing its rows, a filesystem
capacity check requires at least 5 GiB free on the database volume. Failure is
reported as `insufficient_storage`; the uncommitted page does not advance its
checkpoint. Already committed data is retained. The collector never deletes
files to create capacity. This is a reserve check, not a guarantee against
concurrent unrelated disk consumption; callers should monitor available space.
