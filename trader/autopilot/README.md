# Paper autopilot (Phase B source)

Explicit entry points, run from the repository:

```sh
python3 -m trader.autopilot once --database /path/market.sqlite3 --state /path/runtime/autopilot/state.json --radar /path/radar.json --snapshot /path/autopilot.json
python3 -m trader.autopilot run --database /path/market.sqlite3 --state /path/runtime/autopilot/state.json --radar /path/radar.json --snapshot /path/autopilot.json
```

Neither command places exchange orders. `once` runs one tick and decision pass;
`run` uses an anchored ten-second loop, skipping missed slots rather than bursting.
SIGTERM/SIGINT set the stop event and release the persistent-inode instance lock.
The KeepAlive LaunchAgent template and `/paper` page belong to Phase C, not this PR.

The public Futures `allTickers` endpoint is called once per pass, with an eight-second
deadline and no in-pass retry. It provides last-trade prices, used here as paper
marks, not exchange liquidation marks. Only open symbols and XBT/ETH/SOL are retained.
Protocol reference: [KuCoin Futures allTickers](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-tickers).

On restart, missing completed one-minute candles for open symbols are ingested for
the last six hours through the existing rate-limited public client. Existing rows
are never overwritten. Stored complete candles wholly after each bot watermark
are applied in order, with candle close timestamps. An overlapping partial minute
is skipped: its intraminute ordering after the last observed tick is unknowable.
Older gaps without stored candles remain gaps. Recovery errors block new trading
decisions until recovery succeeds; requests retry on the five-minute cadence.

Funding is valued on the pre-update position/price, looking up the latest stored
rate at or before each crossed boundary. Stored fractional rates become percentages
for Phase A. If no stored rate exists, the bot spec rate is used. Artificial funding
valuation does not increment the market-update range streak.

Private state contains immutable Phase A engine dictionaries inside wrappers with
source section, original slot, undeployed reserve, latched risk flags and PnL samples.
Reserve is included in displayed bot equity and drawdown; total portfolio equity
starts at 10,000 and does not count allocated margin/reserve as new deposits.
The state retains 30 days of closed bots and minute equity samples; aged closed net
is carried forward. The snapshot exposes 20 closed bots, up to 2,000 portfolio
samples and 120 points per bot. Direction totals cover retained history and use
the sum of per-bot grids/hour. Historical all-time net survives retention.

State and snapshots use private atomic replacement. Pending events are checkpointed
before appending; monotonically increasing event IDs deduplicate crash retries.
`events.jsonl` sits alongside state when its parent is named `autopilot`, otherwise
inside an `autopilot` child directory. Logs rotate by UTC day and retain 30 days,
including idle periods. Readers cap each daily file at 2 MiB and return at most
500 events; an oversized day fails closed. Private state reads have a 64 MiB bound.

Health reports the age of the oldest required symbol's last trade, API success,
heartbeat and radar age. Decisions run every five minutes or after radar mtime
changes. Invalid radar suppresses openings/label changes but permits safety/age
closures. Scan IDs derive from radar `asof_ms`, so rereading the same scan cannot
cause a false two-scan drop. Stale radar (>120 min) suppresses new candidates.

Telegram is opt-in with **both** `--telegram-chat-id` and `--telegram-state`.
Use `scripts/credential-exec.py` with the existing private credential file to supply
`DLS_TELEGRAM_BOT_TOKEN`; tokens are never written to state, snapshot or event logs.
Health notifications take priority, with at most one five-second transport attempt
per pass. Successful sends are checkpointed; remaining OPEN/CLOSE events survive
retry. The 08:00 Europe/Bucharest summary catches up once if the process starts
later that day, with one direction per line and the day's best/worst closed bot.
Tick scheduling can skip a slot when bounded transport is slow; no catch-up bursts.

All automated verification uses synthetic fixtures and injected clients/clocks.
No daemon, installed agent, provider request or Telegram delivery is run by tests.
