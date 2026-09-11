# Continuous paper research and reporting

Dan explicitly replaced the 48-hour stop on 11 September 2026. The existing two
paper accounts continue without resetting balances, positions, costs or history.
An audit boundary never liquidates or freezes a position. Existing position loss,
daily equity loss, stale-data and implementation-integrity controls still apply.

## Development convention (Phase 1)

New runs from this checkout use one civil-day convention: Europe/Bucharest
00:00 to the following 00:00. The engine halt day, completed daily audit and
daily chart buckets use the shared `calendar_day.py` contract. Other configured
time zones are rejected, and this helper is included in the experiment seal.

Daily event, observation and error windows are `[start, end)`: an event stamped
exactly at midnight belongs to the new day. Equity valuation still uses the last
available post-tick mark at or before each boundary and discloses its timestamp;
that valuation convention can put midnight execution costs into an adjacent
window's equity change. Closed-event totals follow the civil-day convention.
Weekly reports remain Monday at 09:00 with `(start, end]` membership, and full
audits remain 48 elapsed hours with `(start, end]` membership. Civil days across
Bucharest daylight-saving changes contain 23 or 25 hours, not always 24.

Archive filenames and retention cutoffs remain **UTC**, independently of the
reporting day. A local daily report can therefore load parts of two UTC archives.
The historical v1 service remains untouched and continues its original 09:00
daily reporting schedule below; these development rules do not migrate or reseal it.

## Existing v1 cadence (Europe/Bucharest)

| Work | Schedule |
|---|---|
| Deterministic paper-account cycle | Every five minutes, with scheduler polling tolerance |
| Dashboard portfolio snapshot | Every 30 minutes; page checks for updates every minute |
| Full operational audit | Every 48 elapsed hours from the original start |
| Daily period summary | Every day at 09:00 |
| Weekly period summary | Monday at 09:00 |
| Grok delivery and operational review | Every 30 minutes |

The first full audit covers the original start, 11 September 02:11:55, through
13 September 02:11:55. The first daily summary is due 11 September at 09:00;
the first weekly summary is due Monday 14 September at 09:00. Initial calendar
periods are partial and begin at the actual run start, not invented earlier data.
Daily and weekly boundaries follow local daylight-saving time; full audits remain
exact 48-hour intervals.

The local service generates due archives even if Grok is unavailable. Grok reads
pending reports at its next half-hour review and posts them in its own conversation,
so message delivery may follow archive generation by up to one review interval.
It acknowledges report IDs only after delivery and checks conversation history
before retrying a message. No reports are sent outside that bot conversation.

## Reports

The existing page at <http://127.0.0.1:8873/> shows continuous operation, time until
the next full audit, and an archive of HTML, JSON and Markdown reports. Files live
under `~/Sandbox/grokbot/zmarty-paper-runtime/audits/`. Each report has a fixed
period ID and explicit start/end/generation timestamps. Repeated checks do not
generate duplicate reports or reset reporting periods. Bounded catch-up generates
missed reports after interruptions; gaps remain disclosed.

Audits compare baseline and liquidation-filter accounts, including observed equity
change, closed-trade net PnL, paid fees, funding disclosure, drawdown on recorded
marks, wins/losses, entries/additions/exits, risk events, rejected signals, provider
coverage and missing observations. Boundary equity uses the latest recorded mark
at or before each boundary and discloses its age. Funding allocated across a period
boundary is not inferred from a lifetime close total. The reports do not claim a
new test-suite run or proven profitability without evidence.

Old completed UTC days are archived under `experiment-archive/`. The hot ledger
keeps today and three complete prior UTC days. Full account state, cumulative
statistics and lifetime equity history remain intact; period audits load required
archived observations and events. Missing/corrupt archive evidence causes a visible
audit failure rather than a silently incomplete result. Archive writes precede the
hot-state commit and can safely be retried after a crash.

## Operation

The existing dedicated launchd service keeps its original identifier
`com.danslab.zmarty-paper48` to avoid creating a second writer. It runs the continuous
controller, dashboard and report scheduler. The original single-account CLI remains
paused. Sleep prevention now lasts for this service process; keep the Mac powered
and the user logged in. Terminating the service releases its sleep assertion.

```sh
python3 paper_grid/experiment.py report
python3 paper_grid/audits.py pending
python3 paper_grid/audits.py generate --paper
python3 paper_grid/audits.py ack --paper --id REPORT_ID
```

The one-time authorized migration is `experiment.py continue-running --paper`.
It removes the deadline, preserves both accounts and records the new implementation
seal. Repeating it cannot erase state or override a manual/integrity freeze.
`freeze --paper` remains available for an explicit operator stop. There is no live
trading mode. Report generation does not tune strategies or alter paper positions.

The earlier [48-hour specification](RUN_48H.md) and [activation record](ACTIVATION_48H.md)
are historical. This document supersedes their automatic deadline behavior.

## Fixed development timezone

The development engine accepts only `timezone = Europe/Bucharest`. This field
is a compatibility assertion, not an operator-selectable timezone; other values
are rejected so halt and reporting days cannot diverge. UTC archive partitioning
and the disclosed post-tick valuation convention remain unchanged.
