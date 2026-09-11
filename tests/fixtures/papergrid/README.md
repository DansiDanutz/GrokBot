# Synthetic paper grid fixtures

These invented prices are offline unit-test inputs, not exchange observations.
`oscillation.json`, `trend_up.json` and `trend_down.json` each contain 200
one-minute OHLC updates. `ticks.json` supplies a repeated price path.
Tests verify deterministic accounting and input immutability, not trading returns.

`ray_20260911.json` is the exception: 418 recorded RAYUSDTM one-minute OHLC
candles extracted read-only from the existing Phase 2 market SQLite database.
Query window: 2026-09-11 08:20 UTC inclusive to 15:20 UTC exclusive, sorted
by candle timestamp. Missing minutes remain missing; no prices are fabricated.
This bounded regression fixture was explicitly requested in Claude's conditional
gate on issue #16, comment 5637070434. Test opening price is the first candle's
open (1.5852), with bot timestamp one millisecond before that candle.
