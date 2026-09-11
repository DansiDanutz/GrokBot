# Synthetic paper grid fixtures

These invented prices are offline unit-test inputs, not exchange observations.
`oscillation.json`, `trend_up.json` and `trend_down.json` each contain 200
one-minute OHLC updates. `ticks.json` supplies a repeated price path.
Tests verify deterministic accounting and input immutability, not trading returns.
