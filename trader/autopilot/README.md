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
inside an `autopilot` child directory. `DECISION` rows record each distinct open,
close or rejected candidate with the entry-time radar score, direction, expected
grid rate, range width, funding rate, KuCoin health flag and numeric rule codes.
The entry context is retained on the private bot wrapper so close outcomes join to
the conditions that produced them. Logs rotate by UTC day and retain 30 days,
including idle periods. Readers cap each daily file at 2 MiB and return at most
500 events; an oversized day fails closed. Private state reads have a 64 MiB bound.

The public snapshot exposes only the newest 50 decision rows from the last 24 hours
through the same closed-vocabulary projection as other events. The daily review
uses the private retained log to report completed outcomes by fixed 0-100 score
quartile and funding sign, plus rejection-code frequency. The generated doctrine
lists which observed entry profiles made or lost paper money; open trades are
counted as entries but excluded from outcome totals until they close.

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

## Scored watchlist

The radar and autopilot share `trader/radar/scoring.py`. Each qualifying row has a
0–100 score with numeric measurements and fixed reason codes. The complete radar
row universe is eligible, including rows beyond the display's eight-per-section
limit. Qualification retains liquidity, valid profile/range and minimum expected
rate checks; current bot occupancy and cooldown do not remove a coin from the radar.

Persisted core and bench each hold up to five entries. Distinct increasing scan IDs
refresh scores/directions once; rereads do not advance misses. Core begins with the
five highest scores. A challenger must lead the lowest core score by at least ten
points after that seat has held for two hours. At most one promotion occurs per
scan after startup. All twice-missing core coins drop immediately; multiple empty
seats refill one per scan. Each post-start promotion counts once in daily swaps.
Same-symbol direction changes preserve seat age. Score ties break by symbol.

Only qualifying current core coins can open a new paper bot. Total open bots are
capped at five; preferred 2/2/2 direction slots borrow up to four of one direction.
Demotion does not close a bot. Its existing label/range/stop/drop/age rules continue;
it cannot reopen from bench. Older radar scans cannot drive labels or admission.

Snapshots include core/bench, last 48 watchlist events, scan ID/time and 30-day swap
timestamps. The shared event journal additionally accepts PROMOTE, DEMOTE, DROP and
DIRECTION_CHANGE with validated replacement symbols and numeric scores; these are
watchlist events, not exchange instructions. State without watchlist fields is
initialized on its next valid scan without changing existing engine accounting.

Changed scans queue an opt-in Telegram watchlist message with top reason codes and
swap scores/margin. Unchanged scans are silent. Failed messages preserve each scan's
numeric payload across restart and newer scans (up to 720 entries / 30 days), with
oldest-first delivery after health alerts. Daily summaries include local-day swaps.
Phase C renders the score reason templates and watchlist event feed; this phase
changes no public page, installed agent or publisher.

Agent influence (T8) is off by default (`constants.AGENT_INFLUENCE_ENABLED`, or the
learned rule `agent_influence_enabled`). Switched on, the desk reads ONE sanitized
file written by the team's operations steward and lets the eight review roles
reorder (BOOST, bounded delta inside a radar section) or remove (VETO, one scan)
candidates the deterministic policy already admitted. It cannot admit a coin, and
every structural and risk gate still runs afterwards. Any schema, roster, expiry
or duplicate problem ignores the whole file and logs one ERROR event. Applied
influences are DECISION events with `action='influence'`; a veto that turned away
a fillable entry also emits a skip, so the counterfactual replay prices it. See
[docs/agent-influence.md](../../docs/agent-influence.md).
