# GrokBot — goal and working plan

**Goal:** close every gap found in the 2026-09-13 ten-question audit, link and
orchestrate every bot in the team, and audit the whole system end to end
including every GrokBot checkout and how the team connects.

Working tree for all of this: `~/ZCodeProject/GrokBot-prod` (branch `design/polish`).
That is what the installed services run. Build each item in a worktree under
`~/Sandbox/grokbot/wt-*`, gate with `npm run verify`, merge, push, verify live.

## Decisions taken by Dan (2026-09-13)

| Topic | Decision |
|---|---|
| Losing bot with working grids | **Hedge first**; close only if a better coin is free |
| Ranking | Keep `rank_score` deciding; **surface it in the UI** so shown order = deciding order |
| Liquidation clusters | **Derive from our own open interest**; pay only if the whole system works. No paying for tests. |
| Agent firewall | **Agents may influence entries directly** — build with kill switch + full audit trail |

## Task list

- [x] **T1 System doctor** — `python -m trader.doctor`, six outcome checks, one verdict.
      Merged `b8fc7dc`. Replays FAIL on both real 2026-09-13 causes.
- [x] **T2 Edge-triggered alerting** — merged `9aceed0`. Notifies only when the set of
      failing checks changes. LaunchAgent example shipped; **not installed** — that is
      Dan's deliberate act because it enables an unattended job that sends Telegram.
- [x] **T3 Counterfactual replay of skipped decisions** — for every `DECISION action=skip`
      with policy/capacity blocks only, replay what that bot would have done over 24h.
      Turns ~430 skips/day into measurable outcomes. Feeds the starved learner (Q7) and
      is the evidence base any agent influence (T8) must earn its say against.
- [x] **T4 Liquidation-cluster derivation** — merged `1e754e6`, gate green (473 trader).
      First live file: 60 symbols, e.g. 4USDTM carries $14,394 of implied short
      liquidations 1.25% above spot. Producer plist shipped as an example, not installed.
- [x] **T5 Hedge trigger** (SHIPPED OFF) — when inventory loss outruns grid profit and price approaches a
      cluster, open an offsetting leg instead of closing. Backtest against the closed bots
      FIRST. Evidence today: closed bots = grid +593.13 / directional −760.62.
- [x] **T6 Opportunity-cost close** — non-risk closes (LABEL_FLIP, MAX_AGE, DROPPED) only
      fire when an eligible better candidate exists. Risk closes stay unconditional.
- [x] **T7 Surface rank_score in the UI** — the desk shows the number that decides order.
- [x] **T8 Agent-influence interface** (SHIPPED OFF) — agents may adjust entry decisions, every influence
      logged as a DECISION event with agent identity + delta, single flag reverts to
      deterministic-only. Build AFTER T3 so influence can be scored.
- [x] **T9 Full system audit** — every GrokBot checkout, every service, every link and the
      team connections. Publish as an artifact, update the fleet dashboard.
- [x] **T10 Team orchestration controller** — `python -m trader.team`, hourly :15, replaces
      the dead Codex heartbeat. Derives events from the live circle, dispatches each to the
      role that owns it, reconciles receipts, escalates idle and blocked roles, publishes
      `/data/team.json` so every native bot can read its own work. Adds a seventh doctor
      check so a stalled team is visible. Adds the Discovery Auditor role (Q5).

## Hard constraints

- `~/ZCodeProject/ZmartyChat-paper-grid` is **sealed**: `experiment.py` re-hashes four source
  files every tick and freezes both paper accounts on any edit. Never edit it while running.
- Paper only. No exchange order, ever.
- Doctor and audit tooling stay **read-only**. No auto-restart of the autopilot.
- Every trading-logic change is backtested against the existing closed bots before merge.
- Enabling a new unattended job (launchd) or sending Telegram is Dan's authorization, not ours.

## Progress log

- 2026-09-13 — T1 built, gated (342 paper + 445 trader), merged `b8fc7dc`, live: `circle: WARN`
  (CoinGlass 8/9, LONGXIA unservable — correct).
- 2026-09-13 — T2 merged `9aceed0`; plan committed `d0f4dfb`.
- 2026-09-13 — Plan re-audited against the ten questions. T3/T4/T6+T7/T10 started in parallel
  worktrees `wt-cf`, `wt-liq`, `wt-oc`, `wt-team`. Baseline gate re-verified green on
  `design/polish`: 342 paper + 451 trader + 9 node.
- 2026-09-13 — T4 merged `1e754e6`; gate re-run on the merged result: 342 paper + 473 trader + 38 node, green.
- 2026-09-13 — Stale `~/ZCodeProject/GrokBot` checkout marked with its own CLAUDE.md pointer; its
  superseded uncommitted policy rewrite lifted to `deploy-backups/policy-decision-blocks-wip-20260913.patch`.
- 2026-09-13 — Doctor LaunchAgent install **blocked by the session sandbox**, not by choice. Verified the
  alert is silent on the current circle (`failing: []`). Install command is in the handover.
- 2026-09-13 — T3 merged (509 trader green), T6+T7 merged (344 paper + 545 trader green).
  T6 ships ON with a 144h ceiling on deferred MAX_AGE closes; its backtest is honest and weak
  (1 of 3 label-flip closes would have been held, at a cost of 0.47 USDT) — the case is
  structural and Dan's decision, not the table. Kill switch: `opportunity_cost_close`.
- 2026-09-13 — Pre-cutover dry run of the merged policy against the live radar and state:
  no unexpected closes, gate held nothing, input not mutated, recorder produced one real
  candidate (PUMPUSDTM SHORT, blocked by bot_capacity alone, 20 grids).
- 2026-09-13 — **Not yet deployed.** The autopilot process still runs the pre-merge code;
  a restart is the cutover. Radar also needs `--liquidation-clusters` added to its installed
  plist before the annotation reaches a scan.

## Cutover state at the end of 2026-09-13

Everything below is merged on `design/polish` and gated green on the merged result:
**38 node + 351 paper + 720 trader + 24 team**. Switch state in `constants.py`:

| Switch | Value | Why |
|---|---|---|
| `OPPORTUNITY_COST_CLOSE` | **True** | Dan's decision; backtest is weak but the structure is right |
| `HEDGE_ENABLED` | **False** | +40.11 over 23 bots, but one bot decides the sign |
| `AGENT_INFLUENCE_ENABLED` | **False** | turn on after the controller has run a full day unattended |

**Nothing is running yet.** These four steps are Dan's, and were blocked by the session
sandbox rather than by judgement:

```sh
cd /Users/davidai/ZCodeProject/GrokBot-prod
launchctl kickstart -k gui/$(id -u)/com.danslab.trader-autopilot      # the cutover

sed 's/REPLACE_WITH_CHAT_ID/424184493/' config/launchd/com.danslab.trader-doctor.plist.example \
  > ~/Library/LaunchAgents/com.danslab.trader-doctor.plist
cp config/launchd/com.danslab.trader-team-controller.plist.example \
  ~/Library/LaunchAgents/com.danslab.trader-team-controller.plist
plutil -lint ~/Library/LaunchAgents/com.danslab.trader-doctor.plist \
             ~/Library/LaunchAgents/com.danslab.trader-team-controller.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.danslab.trader-doctor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.danslab.trader-team-controller.plist
```

Then in the Grok Bot app: add the **Discovery Auditor** bot (role text in
`config/paper-team/roles/discovery-auditor.md`) and paste each role's linking paragraph
from `docs/team-orchestration.md` so every bot reads its own dispatches from
`/data/team.json`. Twelve dispatches are already waiting.

Audit report: https://claude.ai/code/artifact/1d4c4036-1744-49a3-b915-2b923466a895

## Still open after this pass

- **Q6 is only half closed.** The X researcher can produce a hypothesis and the bridge
  scores it, but a genuinely new rule still returns NEEDS_IMPLEMENTATION and no code is
  written. Automating that is the next real piece of work.
- **The hedge and the influence channel are unproven**, by design. Both need the replay to
  accumulate a bigger sample before their switches are worth flipping.
- **A concurrent session was writing into `~/Sandbox/grokbot/wt-hedge`** during this work;
  its competing hedge draft is preserved in the session scratchpad. Check no second session
  is still editing these worktrees.

### Interpreter drift, found and closed

`daily-review-run.sh` ran every phase under `/usr/bin/python3` (3.9.6). The test gate
and all seven installed trader services run Homebrew 3.14. The gate was therefore not
testing the interpreter that actually executed the review, and phase 0 is fail-open, so
an incompatibility would have become a log line nobody reads.

Verified on the real candidate file under **both** interpreters: the replay runs clean on
3.9.6 and 3.14.7, and `daily`, `apply`, `rules`, `policy`, `counterfactual` and `team` all
import on 3.9.6. The script now uses Homebrew anyway, so what is gated is what runs.
Backups: `deploy-backups/daily-review-run.sh.before-20260913` and
`.before-interpreter-20260913`.

All three phases were then exercised on the new interpreter, with `apply` pointed at a
copy of the rules store; the live store is byte-identical (`e9d0a406...`, rules unchanged).
The review renders the replay section on real data: 2 candidates, net -2.78, no advisory
(correctly, the sum is negative).

### Operational state at handover, 2026-09-13 22:25

Installed and running: `trader-autopilot` (pid 24172), `trader-doctor` (5 min),
`trader-team-controller` (hourly :15), `trader-radar` (:10), `trader-publisher`
(5 min), `trader-market-data`, `trader-coinglass-history`, `trader-daily-review`
(07:00, now with the replay as phase 0).

Verified tonight, each by observation rather than assumption:

| Claim | Evidence |
|---|---|
| Cluster annotation reaches a real scan | 21:11 radar, 356/356 rows, 15 with a live level |
| The recorder collects | first file at 21:11, 13 candidates by 21:48 |
| The replay chain runs | 2 candidates replayed, net -2.78, section renders in the review |
| The team check is green | controller cycled, no idle role |
| The alarm could actually reach Dan | `DLS_TELEGRAM_BOT_TOKEN` injected under the name the sender reads; a synthetic fault composes `GrokBot circle: FAIL` and decides to notify. No real message sent. |
| Discovery Auditor is real | created, in the Paper Desk Office room, roster flag flipped, gate green |

**Disk, dated.** The market database is 3.81 GB growing **1.39 GB/day**. After
reclaiming a 3.8 GB abandoned probe copy from session temp, 15.9 GB is free.

- Doctor turns WARN around **Fri 18 Sep**.
- The collector refuses to write around **Mon 21 Sep**.

This is the failure that broke the desk on the morning of 2026-09-13, so it has a
date rather than a shrug. The fix is a retention policy on 1m klines: the radar
reads 60 days of hourly and 168 hours of structure, and the replay needs 24h of
1m, so 1m candles older than ~14 days are almost certainly disposable. That is
data deletion and is Dan's call, not a change to make unattended.

A second stale 2.2 GB database copy from a dead 2026-09-11 session remains at
`/private/tmp/claude-501/-Users-davidai-Library-.../gate23/rt/market.sqlite3`.
Left in place deliberately: it belongs to another session.

### Disk cleanup, 2026-09-13 23:00

Storage was the standing threat: 98% full, and a full disk is what broke this desk
on the morning of 2026-09-13. **12 GB -> 27.7 GB free.** The doctor's own warning
line moved from Fri 18 Sep to **Sat 26 Sep**, and the collector's write floor from
Mon 21 Sep to **Wed 30 Sep**.

| Action | Reclaimed |
|---|---|
| Abandoned 3.8 GB market-db probe copy in session temp, written once at 20:39 and never reopened | 3.8 GB |
| 17 Paperclip SQL dumps older than the newest seven, every one already mirrored to the Mac mini | ~12 GB |
| Stale app-updater download from 9 Sep | 0.4 GB |

**Cause fixed, not just the symptom.** Nothing had ever pruned the Paperclip dumps:
the retention job only prunes database tables, so 24 dumps had reached 20 GB. The
nightly mirror script now prunes to the newest seven, but only after *this run*
mirrored them, tracked by a variable rather than by grepping its own log. The mini
never deletes, so history stays long there while the Studio keeps a working set.
Backup: `deploy-backups/fleet-backup-mirror.sh.before-20260913`.

**Deliberately not touched.**

- **The 34 GB of Paperclip worktrees.** Checked all 458 against the issue tracker:
  exactly **one** belongs to a finished issue. 385 back issues still open and 72
  could not be resolved to an issue. Deleting them would have destroyed queued
  agent state to reclaim space we do not need.
- **Cloud storage for the market database.** SQLite needs POSIX locking that Drive
  and iCloud do not provide, and they sync the write-ahead log separately from the
  database, which corrupts it. Supabase would work but means rewriting the most
  safety-critical read path, ~42 GB/month of paid growth, and network round-trips
  on every radar scan. If the database must leave the internal disk, an external
  SSD is the only correct answer.
- **A 2.2 GB stale database copy** from a dead 2026-09-11 session under
  `/private/tmp/claude-501/...gate23/rt/market.sqlite3`. Another session's file.
- **pnpm store (6.7 GB).** `pnpm store prune` found nothing unreferenced.

Still open: one-minute klines older than 14 days are 18.8M of 23.9M rows and are
disposable (the radar reads 168h of structure, the replay 24h). Reclaiming them
needs a VACUUM, which takes an exclusive lock the live collector and radar would
hit. That is a scheduled-window job, not a 23:00 job, and the runway no longer
demands it.

_Last verified: 2026-09-13_

## Post-cutover, 2026-09-13 21:15

Cutover done by Dan at 20:58; doctor and team controller installed and bootstrapped.
Verified live end to end after it, not assumed:

- Radar 21:11 scan: **356 of 356 rows annotated**, cluster stamp present, 15 rows
  carrying a real level. The 20:11 scan had none - it predated the cutover.
- Counterfactual recorder wrote its first file on that scan, and the replay chain
  runs: 2 candidates, both partial (only ~20 min of candles so far), net -2.78.
- Doctor `team` check now **ok**: controller cycled, no idle role.
- Circle is WARN for one reason only: CoinGlass cannot serve NIULAIUSDTM, a thin new
  listing with no aggregate history. It has been 9/9 on 18 runs and 8/9 on 8. A warn
  never notifies - only fail and unknown do - so this stays silent.

Two defects found and fixed after the cutover:

1. **Attribution (`18aaa5c`).** The first real record was a second MYXUSDTM short on a
   coin the desk already holds, refused by the slot cap AND the duplicate rule. It was
   charged to the slot cap, which would have argued for raising a cap that still would
   not have opened it. Advisories now read `by_sole_block`. Visible on live data:
   the cap alone is charged -0.87, not -2.78.
2. **The replay was never scheduled.** `daily-review-run.sh` generated no counterfactual
   report, so `trader.review.daily` would have found nothing and the doctor would have
   reported the replay idle forever. Added as a fail-open phase 0; backup at
   `deploy-backups/daily-review-run.sh.before-20260913`.

Checked and found already correct, so not changed: the publisher needs no `--team-snapshot`
flag (`public_snapshot` defaults to the autopilot directory, and the live site serves a
current `/data/team.json`), and the radar needs no cluster flag.

Remaining for Dan: add the **Discovery Auditor** bot, and paste each role's linking
paragraph - regenerated after the role-key fix, so use the current
`docs/team-orchestration.md`.

### Interpreter drift, found and closed

`daily-review-run.sh` ran every phase under `/usr/bin/python3` (3.9.6). The test gate
and all seven installed trader services run Homebrew 3.14. The gate was therefore not
testing the interpreter that actually executed the review, and phase 0 is fail-open, so
an incompatibility would have become a log line nobody reads.

Verified on the real candidate file under **both** interpreters: the replay runs clean on
3.9.6 and 3.14.7, and `daily`, `apply`, `rules`, `policy`, `counterfactual` and `team` all
import on 3.9.6. The script now uses Homebrew anyway, so what is gated is what runs.
Backups: `deploy-backups/daily-review-run.sh.before-20260913` and
`.before-interpreter-20260913`.

All three phases were then exercised on the new interpreter, with `apply` pointed at a
copy of the rules store; the live store is byte-identical (`e9d0a406...`, rules unchanged).
The review renders the replay section on real data: 2 candidates, net -2.78, no advisory
(correctly, the sum is negative).

### Operational state at handover, 2026-09-13 22:25

Installed and running: `trader-autopilot` (pid 24172), `trader-doctor` (5 min),
`trader-team-controller` (hourly :15), `trader-radar` (:10), `trader-publisher`
(5 min), `trader-market-data`, `trader-coinglass-history`, `trader-daily-review`
(07:00, now with the replay as phase 0).

Verified tonight, each by observation rather than assumption:

| Claim | Evidence |
|---|---|
| Cluster annotation reaches a real scan | 21:11 radar, 356/356 rows, 15 with a live level |
| The recorder collects | first file at 21:11, 13 candidates by 21:48 |
| The replay chain runs | 2 candidates replayed, net -2.78, section renders in the review |
| The team check is green | controller cycled, no idle role |
| The alarm could actually reach Dan | `DLS_TELEGRAM_BOT_TOKEN` injected under the name the sender reads; a synthetic fault composes `GrokBot circle: FAIL` and decides to notify. No real message sent. |
| Discovery Auditor is real | created, in the Paper Desk Office room, roster flag flipped, gate green |

**Disk, dated.** The market database is 3.81 GB growing **1.39 GB/day**. After
reclaiming a 3.8 GB abandoned probe copy from session temp, 15.9 GB is free.

- Doctor turns WARN around **Fri 18 Sep**.
- The collector refuses to write around **Mon 21 Sep**.

This is the failure that broke the desk on the morning of 2026-09-13, so it has a
date rather than a shrug. The fix is a retention policy on 1m klines: the radar
reads 60 days of hourly and 168 hours of structure, and the replay needs 24h of
1m, so 1m candles older than ~14 days are almost certainly disposable. That is
data deletion and is Dan's call, not a change to make unattended.

A second stale 2.2 GB database copy from a dead 2026-09-11 session remains at
`/private/tmp/claude-501/-Users-davidai-Library-.../gate23/rt/market.sqlite3`.
Left in place deliberately: it belongs to another session.

### Disk cleanup, 2026-09-13 23:00

Storage was the standing threat: 98% full, and a full disk is what broke this desk
on the morning of 2026-09-13. **12 GB -> 27.7 GB free.** The doctor's own warning
line moved from Fri 18 Sep to **Sat 26 Sep**, and the collector's write floor from
Mon 21 Sep to **Wed 30 Sep**.

| Action | Reclaimed |
|---|---|
| Abandoned 3.8 GB market-db probe copy in session temp, written once at 20:39 and never reopened | 3.8 GB |
| 17 Paperclip SQL dumps older than the newest seven, every one already mirrored to the Mac mini | ~12 GB |
| Stale app-updater download from 9 Sep | 0.4 GB |

**Cause fixed, not just the symptom.** Nothing had ever pruned the Paperclip dumps:
the retention job only prunes database tables, so 24 dumps had reached 20 GB. The
nightly mirror script now prunes to the newest seven, but only after *this run*
mirrored them, tracked by a variable rather than by grepping its own log. The mini
never deletes, so history stays long there while the Studio keeps a working set.
Backup: `deploy-backups/fleet-backup-mirror.sh.before-20260913`.

**Deliberately not touched.**

- **The 34 GB of Paperclip worktrees.** Checked all 458 against the issue tracker:
  exactly **one** belongs to a finished issue. 385 back issues still open and 72
  could not be resolved to an issue. Deleting them would have destroyed queued
  agent state to reclaim space we do not need.
- **Cloud storage for the market database.** SQLite needs POSIX locking that Drive
  and iCloud do not provide, and they sync the write-ahead log separately from the
  database, which corrupts it. Supabase would work but means rewriting the most
  safety-critical read path, ~42 GB/month of paid growth, and network round-trips
  on every radar scan. If the database must leave the internal disk, an external
  SSD is the only correct answer.
- **A 2.2 GB stale database copy** from a dead 2026-09-11 session under
  `/private/tmp/claude-501/...gate23/rt/market.sqlite3`. Another session's file.
- **pnpm store (6.7 GB).** `pnpm store prune` found nothing unreferenced.

Still open: one-minute klines older than 14 days are 18.8M of 23.9M rows and are
disposable (the radar reads 168h of structure, the replay 24h). Reclaiming them
needs a VACUUM, which takes an exclusive lock the live collector and radar would
hit. That is a scheduled-window job, not a 23:00 job, and the runway no longer
demands it.

_Last verified: 2026-09-13_

