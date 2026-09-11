# DansLabTrader paper operations

This repository consolidates the paper research engine, public dashboard and
publisher source. Copying that source here does **not** migrate the running Mac
services, replace their state, or install a second worker. The existing local
service state remains authoritative.

## Components and active locations

| Component | Current location or identity |
|---|---|
| Native desktop assistant | Installed Grok Bot app, bot **Dan’s Senior Developer**, connection **Mac Studio — Dan’s Lab** |
| Repository workspace app | Separate OpenMausBot OSS app at <http://127.0.0.1:8871/> |
| Continuous paper dashboard | <http://127.0.0.1:8873/> |
| Public snapshots | <https://danslabtrader.vercel.app/> |
| Active source checkout | `/Users/davidai/ZCodeProject/ZmartyChat-paper-grid` |
| Paper state and full audit archive | `/Users/davidai/Sandbox/grokbot/zmarty-paper-runtime` |
| Publisher state | `/Users/davidai/Sandbox/grokbot/vercel-publisher` |

The native Grok Bot routine is **Zmarty paper — continuous review & reports**.
Its reusable instructions are in
[paper-review-instructions.md](../config/paper-review-instructions.md). That file
documents the routine; committing it does not update the installed app's private
configuration database. No private skills database, account ledger, credentials or
runtime logs belong in Git.

The active LaunchAgent `com.danslab.zmarty-paper48` runs:

```sh
/opt/homebrew/bin/python3 /Users/davidai/ZCodeProject/ZmartyChat-paper-grid/paper_grid/server.py \
  --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime --port 8873
```

Its historical label does not imply a 48-hour stop. The separate
`com.danslab.trader-publisher` LaunchAgent invokes the publisher from the same
active checkout every 1,800 seconds. It exports allowlisted snapshots and deploys
them to Vercel. Publication failure leaves the local paper engine running and the
last successful public snapshot available. The webpage polls every minute, but
new public data still depends on successful half-hour publication from the Mac.

## Schedule and reporting

All calendar times below use **Europe/Bucharest**.

| Work | Cadence |
|---|---|
| Paper-account cycle | Five minutes |
| Portfolio snapshot, publisher and Grok review | 30 minutes each; not necessarily simultaneous |
| Full audit | Every 48 elapsed hours from the original run start |
| Daily summary | Every day at 09:00 |
| Weekly summary | Monday at 09:00 |

The original start was **11 September 2026 at 02:11:55**. The first full audit is
due **13 September 2026 at 02:11:55**, the first daily summary on **11 September
at 09:00**, and the first weekly summary on **14 September at 09:00**. These are
report boundaries, not liquidation or freeze deadlines. Daily and weekly periods
follow local daylight-saving time; full audits use elapsed hours. Initial partial
periods and missing observations must remain visible.

The local worker generates due reports independently of Grok. Grok reads pending
reports and posts summaries in its own conversation, then acknowledges each ID
after actual delivery. Public report links become available only after a successful
publisher run. Reports compare the independent baseline and liquidation-filter
paper accounts, including equity, net results, fees, funding, observed drawdown,
risk exits and data coverage. They do not establish future profitability.

At initial activation, both accounts held 1,000 simulated USDT and had no trades.
That is historical activation evidence, **not** a current balance or performance
claim. Read the active report for present results. No real exchange orders are
part of this system.

## Inspect without changing the experiment

```sh
python3 /Users/davidai/ZCodeProject/ZmartyChat-paper-grid/paper_grid/experiment.py report \
  --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime
curl --fail --silent --show-error http://127.0.0.1:8873/api/health
python3 /Users/davidai/ZCodeProject/ZmartyChat-paper-grid/paper_grid/audits.py pending \
  --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime
```

Publisher health is recorded in
`/Users/davidai/Sandbox/grokbot/vercel-publisher/publisher.json`. Read its sanitized
status, timestamps and failure category; do not inspect or publish the adjacent
authentication directory. Report a new publishing failure or a last successful
publication older than 45 minutes. Local `/api/health` is live worker evidence;
public `/data/health.json` is a timestamped static snapshot.

## Source and runtime boundary

The running experiment seals `engine.py`, `market.py`, `coinglass.py` and
`experiment.py` in the active checkout. Editing those active files can trigger an
integrity freeze. Consolidation should preserve their bytes. Do not redirect
LaunchAgents, restart services, reseal the controller, reset accounts or run the
repository copy as a second writer as part of a source-only update.

A later runtime migration needs its own reviewed cutover and verification of the
single-writer boundary, existing ledger, implementation seal, publisher paths and
report continuity. Until then, use the active paths above for operations and this
repository for source review. See [the public hosting guide](../paper_grid/PUBLIC_WEB.md)
and [continuous reporting contract](../paper_grid/CONTINUOUS.md) for implementation
details.

The import manifest is [paper-source.lock.json](../config/paper-source.lock.json).
LaunchAgent templates under `config/launchd/` and
`config/vercel-publisher.example.json` are examples only and are not installed.
Resolve `__PYTHON__`, `__CHECKOUT__`, `__RUNTIME__` and `__PUBLISHER_STATE__` only
during a separate cutover. Never bootstrap those templates alongside the active
services or replace the existing same-label jobs as part of reviewing this import.

## Phase 0 development source

GrokBot is now the development source. The lock file's original file hashes are
historical import evidence from `6ee309c2134ad88e63c0cd3c021859a6c02df03b`,
not a claim that today's development tree is byte-identical to that import.
The v1 control is frozen against roadmap changes. Before this roadmap,
`e0e10a77` added telemetry to the active source without changing trading decisions;
calling the deployed source exactly `6ee309c2` would omit that recorded boundary.
No runtime inspection or modification is required by Phase 0.

Analytics, telemetry and replay were already committed in `bb2494d` and
`3250c9f` (merged in `a3df61e` and `2889f38`). New development remains in this
checkout until a separately reviewed v2 deployment. Do not copy these fixes into
v1. The secret gate scans a frozen Git index tree when changes are staged and
HEAD otherwise; unstaged tracked changes are rejected. Run both verification
commands after staging each task and before committing.


## Development CoinGlass configuration

Only the development copy reads `PAPER_GRID_SECRETS_FILE`, defaulting to
`~/.openclaw-secrets/paper-grid.env`. It reads the exact `COINGLASS_API_KEY`
assignment. Direct, case-varied and symlink-resolved Desktop paths are rejected
before opening the file, including the iCloud Desktop. Errors omit values.
An override is resolved at call time, so an environment change does not require
reimporting the module. The v1 checkout keeps its own existing configuration.

Dan provisions the private file once, outside this roadmap execution, with mode
0600 inside a private directory. Copy only the existing CoinGlass assignment
using a local editor, without pasting it into a terminal command or chat.
Codex has not read, copied, written or requested the key during Phase 0, and no
CoinGlass request is made by verification. The env override selects an already
provisioned file; it must never be committed to a plist or repository.

_Last verified: 2026-09-11_
