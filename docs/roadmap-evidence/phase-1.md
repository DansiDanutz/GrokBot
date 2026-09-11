# Phase 1 handoff to Claude

Date: 2026-09-11. Scope: roadmap **1.1–1.6** only.
Status: source implementation complete; ready for audit at a fixed, unmerged PR
head. Phase 2 has not started. The PR head, rather than this document's own
commit hash, identifies the complete review tree (including this handoff).

Repository: `DansiDanutz/GrokBot`; branch `codex/roadmap-phase-1`.
Base: audited Phase 0 merge `abaf392233aa307202f92dbeef12eb1d6d8e6c9a` (PR #9).
Tracking issue: https://github.com/DansiDanutz/GrokBot/issues/10.
Review procedure: [fixed-head audit protocol](../grokbot-roadmap-audit-protocol.md).
Codex must remain idle during Claude's separate-worktree review, with no merge
or next-phase work. The protocol supersedes the roadmap's former merge-first wording.

## Task ledger

| Task | Commit | Status and evidence |
|---|---|---|
| 1.1 | `fd7c09c` | Done. Explicit rejection codes and failing numeric context at execution/selection; per-arm/tick audit counts. Cash and notional-cap failures separated. Historical codes remain readable; archived events are not rewritten. |
| 1.2 | `81ca5c9` | Done. Existing per-tick equity persistence retained; tick marks win publication duplicates. Unique timestamps, conflicting marks rejected, explicit estimated coverage. In-window dip test proves sampled drawdown. |
| 1.3 | `f7149c0` | Done. Last 20 traceback frames, frame count/truncation and tick timestamp. Only repo basenames, bounded function labels and line numbers; external paths/messages/locals omitted. Error records and shared constants included in new experiment seals. |
| 1.4 | `ede1ca9` | Done. Audits already called `coinglass.apply_filter`; equivalence tests prove shared decisions, including changed freshness constants. P0-3/P0-4 folded in: iCloud roots refused, descriptor-based no-symlink reads, current UID, regular file and exact 0600 checked before reading, bounded at 16 KiB. |
| 1.5 | `69a2c79`, followup `066de69` | Done. Shared lifecycle metrics in audit/analytics/both dashboards, explicit public projection, rendered synthetic sample. Bounded retained evidence includes inherited pre-experiment archives; missing declared archives fail closed. |
| 1.6 | `11b606f` | Done. Shared Bucharest civil day; halts and daily reports use midnight. Daily events are [start, end), with explicit public convention (unknown for absent/invalid legacy values). DST tested, archive dates stay UTC, weekly remains Monday 09:00 and full audits remain 48 elapsed hours. |

Each implementation commit contains one task only. Task 1.5's separate followup
is an independently gated review correction, not a second roadmap task. This
handoff and the P0-5/P0-6 operator documentation are a documentation-only commit.
No behavior tests were invented for documentation changes.

## Counts and TDD

Baseline: **38 Node + 223 Python = 261 tests**.
Final implementation: **38 Node + 279 Python = 317 tests**.
No dependencies added. The existing 12-scenario state/fill parity golden remains
green; diagnostics and instrumentation do not change those trading outcomes.
All verification uses offline fixtures. Credential tests use synthetic files or
mocked I/O, including the RED guards; no actual keys or paid providers are used.

| Task | RED evidence | Result after integration |
|---|---|---|
| 1.1 | `/tmp/grok-phase1-11-red.log`: 5 tests, 8 failed assertions/subtests on old rejection codes/context | 225 Python; targeted engine/experiment/telemetry/public checks pass |
| 1.2 | `/tmp/grok-phase1-12-red.log`: 4 tests, 2 failures for duplicate estimates/conflicting marks | 229 Python |
| 1.3 | `/tmp/grok-phase1-13-red.log`: 4 tests, 1 failure and 3 errors | 233 Python |
| 1.4 | `/tmp/grok-phase1-14-red.log`: 7 tests, 23 guarded failing subtests against old reader | 243 Python; 27 targeted credential/filter checks pass |
| 1.5 | `/tmp/grok-phase1-15-red.log`: missing new metric module; later review, loader and render RED logs cover unknown history, missing CoinGlass, bounded reads and compact HTML | 265 Python |
| 1.6 | `/tmp/grok-phase1-16-red.log`: 9 failures; replay helper-seal RED; public boundary/label RED: 1 failure, 5 errors | 277 Python; 45 calendar/replay/public checks pass |
| 1.5 review | `/tmp/grok-phase1-15-inherited-red.log`: 8 tests, 2 failures for archived pre-start open and missing declared archive | 279 Python; 8 evidence-reader tests pass |

Tasks 1.4–1.6 first ran RED/GREEN in isolated temporary source copies so that
parallel preparation could not contaminate another task's staged gate. Reviewed
changes were integrated sequentially in this checkout and gates rerun before
committing. Final review used only a synthetic temporary ledger. The inherited
archive finding was reproduced RED before correction. No Claude gate is claimed
from this internal review.

## Synthetic rendered evidence

- [Rendered audit](phase-1-sample.html)
- [Exact JSON](phase-1-sample.json)
- Generator: `paper_grid/metric_sample.py`; requires an explicit safe output directory.

The sample deliberately spans **20 minutes**, using the audit48h renderer with
an explicit synthetic summary. It is not a real 48-hour run or a profitable
strategy demonstration. Prices, contract symbols, fills and funding are generated
locally; no historical/live account ledger is read.

| Baseline metric | Synthetic result |
|---|---|
| Closed trades | 2 (one win, one loss) |
| Closed net PnL | +1.5319479488 USDT |
| Profit factor | 1.497708283 |
| Exposure | 900 / 1,200 seconds = 75%; overlapping positions counted once |
| Mean hold | 900 seconds; 2 known, 0 unknown |
| AAAUSDTM net | +4.609951674 USDT; one add |
| BBBUSDTM net | -3.078003725 USDT |
| AAA observed MAE / MFE | -1.193016503 / +4.609951674 net USDT |
| BBB observed MAE / MFE | -3.078003725 / +0.412727284 net USDT |
| Baseline entry blocked by the saved filter | 1 trade, +4.609951674 USDT outcome |

The blocked-entry cohort is descriptive: it shows the baseline outcomes whose
initial entries failed the contemporaneous filter, not a causal profit/savings
estimate. Unknown entry evidence remains unknown. MAE/MFE are signed lifetime
net-USDT marks after modeled slippage, entry/exit fees and prior-rate funding;
missing observations/depth are disclosed. They do not measure intratick extremes.
No-loss profit factor stays unavailable, not infinity or an invented zero.

Exact regeneration command, run in the repository:

```sh
python3 -c "from pathlib import Path; from paper_grid.metric_sample import write_sample; write_sample(Path('docs/roadmap-evidence').resolve())"
```

Output: none, exit 0. Both committed artifacts are reproducible; a test checks
byte equality and a fewer-than-800-line rendered HTML bound.

## Browser evidence

Command: `python3 tests/manual/phase1_dashboard_evidence.py`.
It creates temporary fixtures and ephemeral loopback ports, serves real analytics
and both dashboards, and never starts the monitor loop. The public reference is
read from the audited Git tree `abaf392`, not the installed runtime. Fixture
servers and browser tabs were closed after checks.

Both dashboards passed at 320, 375, 768, 1024 and 1440 px, including account and
window switching, synthetic metric values, no page overflow and no JavaScript
errors. An inherited 320px selector overflow was corrected with narrow-screen
stacking. Screenshots were compared against the audited reference. Visual verdict:
94/100, pass; stored in `.omx/state/phase-1-dashboard/ralph-progress.json`.
Exact Playwright code/results: `/tmp/grok-phase1-15-browser.log`.

The final calendar-label smoke checked both pages again at 375/1440 px, confirmed
Bucharest labels and +1.53 USDT across two synthetic closes, with no UTC-day labels,
overflow or JS errors. Exact code/results: `/tmp/grok-phase1-16-browser.log`.
The fixture announced `synthetic: true, worker_started: false` and exited with:

```text
Closed synthetic fixtures; no provider, credential or live-runtime access.
```

## Scope, deviations and remaining limits

- **Source only:** no live runtime, v1 checkout, LaunchAgent, existing plist,
  Telegram bot, publisher state or OpenMausBot export was changed. No service
  restart, deployment, orders, paid request or real data collection was performed.
  Production has not adopted these changes; no fresh live health claim is made.
- Some requested instrumentation/filter sharing existed at the audited base.
  It was preserved and strengthened/tested instead of duplicated.
- P0-3/P0-4 are implemented in task 1.4. P0-5/P0-6 are documented in
  `docs/paper-operations.md`: log failure stops the owned child, and doctor repair
  requires the app stopped. P0-1/P0-2 and Dan-only deletion/provisioning/rotation/
  retirement remain operator actions; this phase did not execute them.
- Daily event windows are half-open to place midnight actions on the new civil
  day. Equity still uses last **post-tick** marks at or before boundaries. Thus
  midnight costs may appear in a neighboring valuation window; timestamps and
  this limitation are disclosed. Legacy public reports lacking a convention
  display unknown rather than being relabeled retroactively.
- Retained evidence is capped at 200 MiB and 400 archive days (plus a boundary
  day). Missing declared files, conflicts, unsafe paths or exceeded bounds fail
  closed. Missing earlier data cannot be reconstructed; exposure/hold/excursion
  coverage states must be respected. This phase adds no data backfill.
- New code was kept in small helpers with constants. Existing long controller,
  renderer and analytics functions were not mass-refactored during this bounded
  instrumentation phase. No new runtime or strategy framework was introduced.
- No v2 deployment or Phase 2 work. Claude's review is still required.

## Required gate commands and actual output tails

All commands below ran from `/Users/davidai/ZCodeProject/GrokBot`. Each task's
files were staged first, then both gates ran successfully, then that task was
committed. The secret gate scans the frozen index and rejects unstaged tracked
changes. Logs are local replayable verification evidence, not runtime logs.
Expected negative-test messages (`Replay rejected invalid input...`, `paper audit:
ValueError`) are deliberate fixtures; the suite exits zero.

### Baseline

```sh
npm run verify > /tmp/grok-phase1-baseline-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.778792ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 1129.419625

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 223 tests in 5.608s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-baseline-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Committed-secret gate passed (96 tracked files scanned).
```

### Task 1.1

```sh
npm run verify > /tmp/grok-phase1-11-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.653ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 910.288833

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 225 tests in 5.542s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-11-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (97 tracked files scanned).
```

### Task 1.2

```sh
npm run verify > /tmp/grok-phase1-12-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.766708ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 1045.046542

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 229 tests in 5.563s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-12-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (98 tracked files scanned).
```

### Task 1.3

```sh
npm run verify > /tmp/grok-phase1-13-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.743041ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 903.548333

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 233 tests in 5.666s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-13-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (100 tracked files scanned).
```

### Task 1.4

```sh
npm run verify > /tmp/grok-phase1-14-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.929833ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 920.799541

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 243 tests in 5.575s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-14-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (102 tracked files scanned).
```

### Task 1.5

```sh
npm run verify > /tmp/grok-phase1-15-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.729375ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 983.636833

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 265 tests in 5.527s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-15-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (113 tracked files scanned).
```

### Task 1.6

```sh
npm run verify > /tmp/grok-phase1-16-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (0.812833ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 894.758583

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 277 tests in 5.569s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-16-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (115 tracked files scanned).
```

### Task 1.5 inherited-history review correction

```sh
npm run verify > /tmp/grok-phase1-15-inherited-verify.log 2>&1
```

```text
✔ source lock distinguishes development from the immutable v1 import (1.033958ms)
ℹ tests 38
ℹ suites 0
ℹ pass 38
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 886.78275

> danslab-grokbot@0.1.0 test:paper
> python3 -m unittest discover -s paper_grid -p "test_*.py" -q

Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
Replay rejected invalid input or unsafe/unavailable output; no runtime changes made.
paper audit: ValueError
----------------------------------------------------------------------
Ran 279 tests in 5.614s

OK
```

```sh
npm run verify:secrets > /tmp/grok-phase1-15-inherited-secrets.log 2>&1
```

```text

> danslab-grokbot@0.1.0 verify:secrets
> node scripts/check-committed-secrets.mjs

Staged-secret gate passed (115 tracked files scanned).
```
