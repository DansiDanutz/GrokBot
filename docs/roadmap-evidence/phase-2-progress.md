# Phase 2 acceptance in progress

Phase 1 PR #11 was merged **as audited**, with no new branch commits before merge.
Merge `3db5613` has a byte-identical tree to audited head `032a2a9`.
The local `codex/mac-studio-foundation` branch was fast-forwarded, then
`codex/roadmap-phase-2` created. Issues #8 and #10 were closed with these gates:

- [Phase 0 gate](https://github.com/DansiDanutz/GrokBot/issues/8#issuecomment-5627838756)
- [Phase 1 gate](https://github.com/DansiDanutz/GrokBot/issues/10#issuecomment-5627838556)

Tracking issue: https://github.com/DansiDanutz/GrokBot/issues/12.
**This is not the final Phase 2 handoff and not permission for Phase 3.**
No Phase 2 PR is frozen for audit yet; measured data collection is outstanding.

## Implemented source

Tasks 2.1–2.5 and P1 followups are implemented. Baseline: 38 Node + 279 Python = 317.
Current: **38 Node + 289 paper + 102 trader = 429 tests**. No dependencies added.
[Per-commit gate transcripts](phase-2-gates.md) contain commands and output tails.
Each original task has its own commit; review corrections are separate commits
associated with that same task. Formatting changed no calculation syntax trees;
only authored analytics definition text was then clarified to `[start, end]`.
The existing cached-result branch gives the same inclusive result when no close
occurs on the boundary; it is not a different reported window convention.

```text
7b1bb2b fix(credentials): explain refused symlinks without exposing paths
6742f63 fix(metrics): keep long-running audit reads within needed windows
e60a04f style(metrics): make reporting code easier to audit
59e2cee feat(phase-2.1): preserve validated market history atomically
3286647 feat(phase-2.2): collect resumable public candles without hiding gaps
1ba6b9a feat(phase-2.3): collect public snapshots on an owned cadence
938dbad feat(phase-2.4): distinguish listing facts from observed coverage
271fb2d fix(phase-2.2): respect the observed public candle page limit
39db264 test(phase-2.1): close SQLite fixture handles deterministically
32d98dd feat(phase-2.5): report missing and unverifiable data explicitly
d7eaf09 feat(phase-2.2): bound parallel backfill time and disk usage
177abea fix(phase-2.1): reserve SQLite writes before reading page state
78155d8 fix(phase-2.3): refuse linked or foreign collector locks
02163b3 fix(phase-2.4): reject historical labels on newer registry state
```

P1-1 uses private, rebuildable per-archive event indexes. Full lifetime events are
retained; observations cover actual due report windows, selected trade lifetimes,
cohort entries and preceding valuation marks. A synthetic 400-day archive above
200 MiB passes; a warm same-window report parses one observation archive. Source
stat changes invalidate indexes; schema, symlink and conflict checks remain.
There is no cumulative 200 MiB / 400-day cliff. A single malformed/oversized file
still fails. First-time index construction is linear in history. This is an
acceleration cache with stat identity checks, not cryptographic tamper resistance.

P1-3 documents the fixed Bucharest timezone; P1-5 distinguishes symlink paths
without printing paths or credentials. The separate style commit used existing
Ruff and an AST equality check. No formatter/dependency was installed. Formatting
can expand line spans of inherited functions; no logic refactor was mixed in.

## Real public acceptance workspace

`/Users/davidai/Sandbox/grokbot/market-data/phase-2-20260911`

Only this newly created private workspace is used for Phase 2 data. It contains
`owner.json`, the SQLite DB, public snapshots, probe evidence, logs, source copy
and process manifests. It contains no credentials or account data.

`acceptance.json` records exact commands, owned PIDs, source snapshot, database,
fixed historical end, start timestamps and log paths. Verify both PID and command
before controlling any process; never signal a PID solely from a stale file.
The fixed source copy was exported from **02163b3** (trader sources only),
so later documentation edits cannot change an in-flight collection.

Initial jobs recorded there:

1. `majors-200`: corrected 90-day BTC (`XBTUSDTM`), ETH and SOL 1m/1h backfill,
   four workers, fixed end `1789088400000`, shared 15 weight/second limiter.
2. `collector-smoke`: one complete-universe `--once` cycle. Inspect this before
   starting the actual `--duration-hours 24` acceptance run.

The majors request has now completed: **1,977 pages, 389,357 stored candle
records, 404.903 seconds, zero collection failures**. All three hourly series
have 2,160 candles and zero gaps. Minute-series omissions remain: ETH 1,551;
SOL 2,467; BTC 1,905. A zero-gap minute-history acceptance claim is not supported.
The full current-universe job is now running under `full-universe` in the manifest,
using the same end and 200-slot checkpoint identity. Do not launch a duplicate.

The full-universe smoke finished in **278.232 seconds**, with 522 contracts,
zero request failures and no missed scheduling slots. It warned on 812 missing
candle intervals; 207 symbols had all requested intervals. Both book and full raw
contract/OI snapshots were recorded for 522 symbols. These are data omissions,
not a software crash or authorization to synthesize bars.

The real 24-hour collector is now running as `collector-24h`, started
**2026-09-11 04:44:02 Europe/Bucharest**, with expected completion approximately
**2026-09-12 04:44:02** plus bounded shutdown overhead. Its quality warnings must
be distinguished from failed requests and missed cycles; do not restart a full
24-hour observation solely because a warning produces a nonzero CLI exit.
An ephemeral `caffeinate -i -w` process is tied only to the owned collector PID
to prevent idle sleep; no system power setting or LaunchAgent was changed.

[Public smoke evidence](phase-2-public-smoke.json) records the measured summary,
initial real registry count 522 and a persisted majors quality report (15 passes,
12 unknowns, 3 warnings). Full private/public-source detail is in
`quality-majors.json` and `registry-initial.json` inside the acceptance workspace.
All counts are timestamped interim evidence while the larger run continues.

An hourly Codex continuation is active: `complete-grokbot-phase-2-evidence`.
It monitors only these owned jobs, stays quiet on normal progress, finishes the
reports/PR once the observation period and feasible collection complete, then
disables itself and holds for Claude's audit. Current source is pushed on the
Phase 2 branch; CI at progress head `b284f5f` passed on Ubuntu and macOS.
No Phase 2 PR is open for audit yet. The market-data `.plist.example` remains
source only, with no installed job.

A real public discovery found **687 contract records, 522 active crypto USDT
perpetuals**. Raw discovery is in `contracts.json` with retrieval time. This
crypto-only current universe does not establish delisted-market coverage.
Registry metadata can be seeded with the complete saved response, then refreshed
from collected snapshots; oldest stored candle and listing date remain distinct.

The initial 500-slot public request exposed an effective **200-row** response
cap. Only the newly created backfill process (PID52960) was stopped for correction.
Its partial rows are preserved. New checkpoint identity includes the 200-slot
bound, so old query frontiers cannot skip repair windows. Evidence:
`page-limit-probe.json`. A 200-slot BTC window returned 198 rows, and separate
one-minute rechecks of both missing intervals returned zero rows. Evidence:
`gap-recheck-probe.json`. These are provider omissions, not authorization to
invent bars or assert zero gaps. Final handoff must report actual coverage.

A disk check showed 26 GiB free before the full run. Backfill refuses new fetches
and commits below a 5 GiB reserve. The updater, at most24h, is independently
bounded; no existing data or unrelated files may be deleted to make space.

## Remaining acceptance and final handoff

- Record actual majors and current-universe backfill runtime, row counts, ranges,
  gaps, failed series and resumable checkpoints. Retry recoverable failures with
  the same fixed end; never silently revise or fabricate missing candles.
- Complete and inspect the real 24-hour collector, distinguishing 288 synthetic-clock
  test cycles from measured operation. Record attempts, successes, warnings,
  errors, missed slots, continuity and source timestamp coverage.
- Produce real registry facts (at least 100 entries with listing evidence if the
  provider response supports them) and a persisted quality report. `quality`
  retains unknown arrival order after normalization and unknown funding periods.
- Then write `docs/roadmap-evidence/phase-2.md` per protocol, link/update gate
  transcripts and real evidence, run both gates before its commit, push and open
  the Phase 2 PR. Wait for green macOS/Ubuntu CI on its final head, report that
  frozen head, and stay idle for Claude audit before Phase 3.
- If an external provider cannot supply a required candle, report the limitation
  and task's partial acceptance honestly; do not claim the zero-gap criterion.

## Boundary incident and restoration

A reused subagent retained an old task message and added a 7-line `merge_records`
helper to the prohibited `ZmartyChat-paper-grid/paper_grid/retention.py` before
reading its new assignment. This violated the requested checkout boundary.
The agent immediately removed only its exact insertion by inverse patch.
No pre-insertion hash was captured. Its reported post-restoration SHA256 is:

`56b385efb25726b8c2cd330531554321eb29ff0fd64d77b5b1c3ba04231d2c9e`

That hash matches the pre-existing import manifest's retention hash in
`config/paper-source.lock.json` and this checkout's unchanged retention file.
The parent verified those two development-source references, without inspecting
the live checkout. File timestamps may have changed; byte restoration is the
claim, not an untouched timestamp. No service restart, reseal, plist, Telegram or
publisher action was performed. All subsequent subagent work used isolated source
copies. Future work must avoid reusing cross-checkout task context.

No Dan-only operator action was executed. No real orders, exchange credentials,
CoinGlass calls or paid APIs were used. P1's remaining operator actions stay Dan-only.
