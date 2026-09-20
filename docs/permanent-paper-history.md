# Continuous paper history

The bankroll and `started_ms` continue unchanged across analysis checkpoints.
`trader.autopilot.history_archive` writes private `history.sqlite3` beside the
operational event log. It has no automatic expiry:

- `equity`: original timestamped equity samples, keyed by run start and timestamp.
- `positions`: the full closed wrapper, including accounting, close reason and
  available original decision/setup evidence, keyed by run start and bot ID.
- `events`: original validated events, keyed by run start and event ID; legacy
  events without IDs use a content hash. Events older than the current known
  run are retained under run 0, explicitly unknown provenance.

The first successful checkpoint per process imports existing event files. Already
lost history cannot be recreated. Every subsequent checkpoint archives pending
events before log retention and saves equity/closed wrappers before state
retention. SQLite transactions, FULL synchronous writes and unique keys make
retries atomic and idempotent. Existing records are not overwritten.

Archive failures expose `history_archive_status: BLOCKED` in the snapshot.
Closed wrappers and equity samples remain in state and log pruning is skipped
until retry succeeds. Market/risk processing continues. A prolonged disk failure
still requires intervention: retaining evidence increases operational state size,
and no software can guarantee persistence when all storage is unavailable.
A successful retry reports COMPLETE (checkpoint success, not proof that history
from before installation exists). Back up the database with SQLite's backup API;
this local archive is not an off-machine backup.

Read-only inspection:

```sh
sqlite3 -readonly ~/Sandbox/grokbot/autopilot/history.sqlite3 \
  'SELECT run_ms, COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM equity GROUP BY run_ms;'
sqlite3 -readonly ~/Sandbox/grokbot/autopilot/history.sqlite3 \
  'SELECT run_ms, bot_id, closed_ms FROM positions ORDER BY closed_ms;'
```

This change does not alter the rolling dashboard payload: 30 days of detailed
equity/closed positions, 168 hourly buckets, 72 hours of per-bot samples. A future
bounded archive reader/publication change is needed to display all-time history.
Equity is mark-to-market, not realized balance. Historical balance and exact net
profit per grid require attributable accounting records; do not infer them by
subtracting all bot fees from aggregate grid profit. No strategy, order sizing,
funding calculation, bankroll reset or production restart is included here.
