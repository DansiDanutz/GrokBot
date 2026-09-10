# Bounded 48-hour paper experiment

The run compares two independent copies of the existing virtual account: the
baseline rebound routine and the same routine with a CoinGlass liquidation gate.
Capital is not pooled between accounts. Each has the same starting virtual balance,
at most two positions, 1x exposure and the existing fixed risk limits. Neither account
can send exchange orders. The original account is backed up and paused for this run.

## Schedule and ownership

- A dedicated local launchd service runs `paper_grid/server.py` on 127.0.0.1:8873.
- Its scheduler checks every 30 seconds; the controller permits one paper cycle
  every 300 seconds. The same fresh quotes feed both accounts.
- Discovery refreshes when the Europe/Bucharest date changes. Existing positions
  remain monitored even when their symbols leave the top-five list.
- CoinGlass completed-hour data is cached for 30 minutes. New shortlist names
  require coverage before comparison; unavailable data blocks the filtered arm's
  new risk while preserving quotes used for its exits.
- Published account snapshots and chart samples update every 1,800 seconds.
  The webpage checks for new snapshots and live service health every 60 seconds.
- Grok Bot's `Zmarty 48h paper — 30-minute review` reads the report and health
  every 30 minutes, stays quiet on unchanged idle results, and reports material
  events or failures. The previous daily writer is disabled.
- `start_at` and `end_at` in `experiment.json` are authoritative. The controller
  prohibits further data collection or simulated fills once the deadline is
  reached, and publishes a final frozen report. Grok is instructed to issue one
  final comparison and disable its review routine. The webpage stays available.
- Sleep prevention is scoped to the service PID and the remaining experiment
  time. Power loss or a logged-out user session can still interrupt observations;
  launchd restarts the service after failures in the logged-in session. Missed
  intervals are not reconstructed as fictitious trades.

## Fixed hypothesis

The comparison arm adds one entry/addition gate: the latest completed hourly
liquidation total must be at least three times the mean of the preceding 24–48
completed hours, with at least 60% of liquidations on the long side. Histories must
be contiguous, valid and sufficiently recent. This threshold is an unvalidated
research choice. It does not override the baseline rebound, spread, depth, funding
or risk checks. CoinGlass aggregates Binance, OKX and Bybit data; this is context
from other venues, not KuCoin fills or future liquidation heatmaps.

The experiment keeps the 50/65/85 USDT bounded tranches, 200 USDT cost-basis cap
per coin, whole-position net target of 1 USDT, 10 USDT position loss / 5% price
drop exits, and 30 USDT daily equity-loss halt. Fees, modeled adverse fills and
positive funding costs are included. Losses can exceed exit thresholds between
polls or when insufficient depth delays an exit.

## Accounting and evidence

Both account states, inputs, events, errors and the published report commit in one
atomic JSON file under an exclusive lock. Repeated calls cannot double-settle a
cycle. Invalid configuration or a changed sealed strategy implementation blocks
continued trading. No background agent may tune the strategy during this run.

Final results include realized and unrealized PnL, open positions, costs, drawdown,
trade counts, signal rejection reasons and data coverage. Positions remaining at
the deadline are preserved and valued at the last observed quote, marked as an
estimate; they are not silently closed or removed. A zero-trade run means
insufficient evidence. Forty-eight hours can test operations, not prove profitability.

## Operator commands

Run from the repository root using `/opt/homebrew/bin/python3`:

```sh
python3 paper_grid/experiment.py report
python3 paper_grid/experiment.py tick --paper
python3 paper_grid/experiment.py freeze --paper
```

`start --paper --hours 48` requires an existing compatible account and refuses to
overwrite any existing experiment. There is no reset, automatic extension or live
mode. Freezing preserves evidence; a future experiment requires a separate store.

Runtime: `~/Sandbox/grokbot/zmarty-paper-runtime/experiment.json`.
Service: `~/Library/LaunchAgents/com.danslab.zmarty-paper48.plist`.
Logs: runtime `service.stdout.log` and `service.stderr.log`.
Dashboard: <http://127.0.0.1:8873/>. It serves only the HTML, sanitized report and
health endpoints; no directory listing, arbitrary files or mutation routes.

The CoinGlass key stays in its existing local environment file and is read into
memory only for the fixed official HTTPS endpoint. No credentials are sent to
Grok's cloud computer or placed in the webpage. Supabase is not modified.

## Verification

```sh
python3 -m unittest discover -s paper_grid -p 'test_*.py' -v
python3 -m compileall -q paper_grid
node scripts/check-committed-secrets.mjs
git diff --check
```

The isolated CI paper-grid job runs the same stdlib tests. Browser and native Grok
routine checks supplement offline tests; actual runtime timestamps and health are
recorded separately after activation.
