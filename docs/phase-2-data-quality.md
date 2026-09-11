# Market-data quality

`trader.data.quality.generate` checks a completed `[start, end)` window and
atomically upserts its findings into `data_quality`. Repeating the same check
updates its record rather than creating duplicate findings. `latest(store)`
returns the latest quality report independently of newer collector heartbeat
records. A later v2 `/api/data` route can consume this result; no route or running
dashboard is installed in Phase 2.

```sh
python3 -m trader.data.quality --database /absolute/private/market.sqlite3 \
  --start-ms 1781312400000 --end-ms 1789088400000 \
  --symbols XBTUSDTM ETHUSDTM SOLUSDTM
```

The CLI uses the existing local public-data store; it makes no provider request.
Reports include check time, symbol, interval, requested bounds and detailed
counts. Documented listing time clips pre-listing intervals and is disclosed as
`effective_start_ms`. A listing interval containing no completed candles stays
unknown. The current active universe does not establish delisted-market coverage.

`assess_candles` also accepts original input rows before normalization. It detects
duplicate timestamps (including conflicting values) and backwards arrival order
independently, then measures missing intervals and runs of at least three
consecutive zero-volume bars. A missing interval breaks a zero-volume run.
Missing candles are never synthesized. Gap samples are bounded to 100 contiguous
ranges, with full counts retained.

Persisted candles have a primary-key uniqueness guarantee, so their duplicate
check describes storage integrity. Original arrival order is no longer available
after public response normalization; the database report marks that check
**unknown**, rather than treating a sorted SQL result as proof of source order.

Funding gaps use only explicitly recorded settlement periods. Public history
does not supply historical periods, so those rows retain `period_ms=None` and
schedule coverage stays unknown. Observed time deltas are reported; they do not
prove a fixed eight-hour schedule or coverage before the first/after the last
observed settlement. Known-period missing or irregular intervals are warnings.

`pass` means the stated check found no issue in its evidence; `warn` reports gaps
or zero-volume runs; `fail` reports invalid duplicate/order evidence; `unknown`
means the available records cannot establish that property. These are data
quality findings, not trading signals or proof of profitability.
