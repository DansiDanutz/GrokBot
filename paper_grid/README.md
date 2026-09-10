# Zmarty two-position paper routine

**Active run:** the original single-account routine is paused while the continuous
comparison runs. See [CONTINUOUS.md](CONTINUOUS.md) for its scheduler, CoinGlass
filter, dashboard and 48-hour/daily/weekly reports. The original ledger is preserved.
The local dashboard is at <http://127.0.0.1:8873/> on this Mac.
The public snapshot is at <https://danslabtrader.vercel.app/>; see
[PUBLIC_WEB.md](PUBLIC_WEB.md) for its isolated 30-minute publisher.

An isolated experiment driven by public KuCoin prices. It never loads the main
application, exchange keys or Supabase clients. There is no live mode or private
order endpoint. It does not control KuCoin's native grid bots.

## Behavior

- Discover active USDT-settled, non-inverse perpetual contracts with at least
  1 million USDT in reported 24-hour turnover. Rank by `(high/low - 1) * 100`.
  This explicit range metric is not asserted to be the app's Volatility column.
- Cache five names per Europe/Bucharest day. A forced daily review refreshes the
  shortlist. Every cycle reanalyses the five and up to two held symbols.
- Require contiguous completed hourly candles, a completed rebound in the lower
  part of the 24-hour range, acceptable ATR, spread, funding and quote age.
  The score and rebound room are hypotheses, not probabilities or forecasts.
- Hold at most two long simulations. No qualifying signal means cash. Entry
  also needs sufficient displayed ask depth and plausible room for the net target
  at the rounded contract size. It never increases leverage to force an entry.
- Start with 50 USDT notional. Add up to 65, then 85 after a 1%, then 2% decline
  from the previous fill AND another qualifying rebound. At least five minutes
  between additions. No more than 200 USDT cost basis per coin; rounding down
  can make actual tranches smaller. One entry plus at most two additions.
- Exit the entire position when modeled net profit reaches 1 USDT (used as a USD
  approximation), including entry and exit fees, funding and adverse fill costs.
  Risk exits can lose money; the target is not a floor on all trade outcomes.
- Stop on 10 USDT net position loss or 5% drop from weighted entry. A 30 USDT daily
  marked-equity loss requests exits and blocks new risk for the Bucharest day.
- Rotate only when a qualifying newcomer exceeds the weakest held score by 15,
  the old holding is at least 30 minutes old, its net exit loss is at most 2 USDT,
  and the last rotation was at least 30 minutes ago. The old exit must fill first.
  Every switching loss and cost is recorded.

## Experimental economics

Initial virtual capital 1,000 USDT; 1x fully cash-collateralized linear exposure;
0.06% fee each leg; 0.02% adverse slippage each leg in addition to observed bid/ask.
Quantity respects each contract's multiplier and lot size. Funding is conservatively
accrued pro rata with the previous known positive rate; negative funding receipts
are not credited. This is not actual exchange funding settlement.

No maintenance-margin/liquidation engine, order queue or partial fills is modeled.
Only full orders fitting displayed top-of-book size are simulated. Insufficient
bid depth defers exits and blocks new risk; reports identify the deferred exit.
This conservative restriction is not a guarantee that a real full fill would occur.
Polling misses intrainterval moves and stops can be exceeded or deferred. Do not
interpret these results as a validated high-frequency futures strategy.

The current version runs on KuCoin public data. Existing Supabase snapshots and
CoinGlass history are research references, not live signal inputs; their provenance
and incremental value still need separate evaluation. Nothing is written to them.

## Commands

From the checkout root, using Python 3.11 or newer:

```sh
python3 paper_grid/cli.py scan --paper    # forced discovery + one paper tick
python3 paper_grid/cli.py cycle --paper   # cached discovery + updated signals/tick
python3 paper_grid/cli.py status          # cached account status, no market request
python3 paper_grid/cli.py pause --paper   # stop paper processing; preserve positions
python3 paper_grid/cli.py resume --paper
python3 -m unittest discover -s paper_grid -p 'test_*.py' -v
python3 -m compileall -q paper_grid
```

Runtime defaults to `~/Sandbox/grokbot/zmarty-paper-runtime`, outside the repository:
`account.json` includes the paper ledger, config fingerprint, scan and event history;
`latest.json`/`latest.md` report the state; `observations/` retains inputs for each tick.
`--runtime PATH` selects another isolated store. Never point it at production data.
There is deliberately no reset command and no automatic credential discovery.
Invalid state/config refuses to run. An exclusive file lock prevents overlapping
cycles and atomic replacement commits account changes and events together.
Replayed timestamps cannot double-credit a close. Failed market reads retain positions.

## Grok Bot routines

The following describes the original routine. During the continuous experiment,
both original writers are disabled and Grok's half-hour review reads the new
experiment report; the dedicated local service handles paper cycles.

The installed app calls this exact CLI on **Mac Studio — Dan's Lab**:
daily review at 09:00 Europe/Bucharest uses `scan --paper`; management every five
minutes uses `cycle --paper`. The Mac connection and Grok scheduler must be available.
The LLM reports results but does not select orders or rewrite thresholds.
Routine notifications are intended for activity, failures and decisions; idle
cycles should stay quiet. Native routine activation is verified separately in the app.

Pause via the CLI and disable both native routines to stop future checks. Pausing
does not fake an exit or remove open paper positions. Resume only after reading status.

## Verification scope

Offline tests cover accounting/costs, whole-position targets, bounded adds, stops,
depth rejection/deferred exits, rotation, stale/future quotes, canonical contract
sizes, completed/gapped candles, adapter-to-engine entry, replay/restart, file locks,
atomic-write failure, corrupt state, pause and market outages. CI has an isolated
stdlib job; running it does not start the legacy application.

First live public-data run: 687 contracts returned; 147 liquid contracts qualified
for ranking; top five NIULAI/2U2/NES/BTR/SAGA. No entries passed, virtual equity
remained 1,000 USDT. This is a runtime smoke test, not performance evidence.

Official endpoint references:
- https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-symbols
- https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-tickers
- https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-klines
- https://www.kucoin.com/support/5090571400217
