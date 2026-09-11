# Autopilot offline fixtures

`radar.json` is synthetic: balanced Long, Short and Neutral candidates for policy tests.
`all_tickers.json` transcribes the public response example from KuCoin's
[Futures allTickers documentation](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-tickers).
No live account, token, private state or provider call is used to construct tests.
Runtime tests inject tick clocks and temporary SQLite candles; Phase A supplies
the underlying tick/candle accounting fixtures.
