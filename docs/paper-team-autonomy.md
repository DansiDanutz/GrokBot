# Autonomous paper-team acceptance design

Design reviewed against repository instructions and recorded native observations
on 2026-09-13. This document is an acceptance design, not evidence that integrations
ran. It extends [version 2](paper-team-v2.md) while preserving the deterministic
paper executor and the existing daily 07:00 gated rule writer.

## Minimum integration

Use the **existing Lead-owned hourly routine**, one recoverable cross-room task
board, and the existing steward's **read-only market inputs and isolated evidence outputs**. Give every
assignment an owner, expected result and recorded completion evidence. Verify a
scheduled round and restart recovery before calling the system autonomous. A
new multi-agent framework, exchange connector or execution engine is unnecessary.

The Lead coordinates **Paper Grid Trading Team** and **Paper Grid Research & Data**;
Strategy Manager owns proposal states; existing Dan’s Senior Developer supplies
bounded market/reviewer evidence through its current access. This bridge reports
results to the team and cannot write strategy or grant new account access. The
installed request worker runs bounded retrospective evaluations into its sandbox.

## Gaps between instructions and observed runtime

| Gap | Minimum acceptance condition |
| --- | --- |
| The saved v2 routine is Active and inspected; unattended execution remains unproven. | Inspect the saved current prompt and one actual scheduled run with attributed specialist replies and final synthesis. An Active toggle is insufficient. |
| Lead's older text asks the “other four roles”; later instructions describe eight review roles across two rooms. | Save one consistent roster/routing mandate; dispatch only relevant specialists, with all assigned work visible on the board. |
| The early quiet-on-no-change branch can conflict with due daily research. | Evaluate daily research due state and actionable backlog before deciding there is nothing to do. Quiet user notification must not suppress authorized work. |
| “Read the last board after restart” is an instruction, not verified persistence. | Save and reread a versioned board through an existing supported native memory/artifact surface; after restart, resume an unfinished task and avoid redispatching a completed task for identical evidence. |
| RD-002 delivered the correct open-position packet and SB-001 transport was verified; Supabase tables remain stale and zmarty-axi returned HTTP 503. | Deliver one sanitized packet or a specific per-source failure. Do not claim a working bridge, repaired ingestion or direct native Supabase access without evidence. |
| Steward queries currently cover up to five active symbols, which does not discover new candidate positions. | Review public radar sections, core and bench each hourly cycle; refer candidate-specific evidence needs to the backlog. Keep existing private query limits until a concrete authorized change is implemented. |
| X onboarding is ACCESS_BLOCKED. | Record the failed source/time, use available primary documentation for independent research, and retain X access as a needs item. A missing X post is not a reason to suspend the portfolio review. |
| Manager's register is not connected to the 07:00 applier; novel-rule application is not wired. | Keep this distinction explicit. Existing tier-1 execution is observed through generated records; novel rules stay in evidence/test/shadow development. |

## Autonomous cycle and new-position discovery

Every hourly :15 invocation uses UTC evidence timestamps and Europe/Bucharest
calendar dates for schedule bookkeeping:

1. Recover the last board and daily completion marker. Reconcile actual assignee
   updates and source versions. Missing private state is UNKNOWN, never DONE.
2. Read current autopilot and radar, check freshness and coverage, and obtain a
   bounded steward packet when required. Inspect new eligible radar/core/bench
   candidates, openings, closes, admission failures and changes in net outcomes.
3. Determine independently whether daily research is due, whether a material
   portfolio/data change needs specialist review, and whether a backlog item has
   new evidence or a dependency that became available. Process due work even when
   the portfolio is unchanged.
4. Assign bounded tasks in the appropriate room. Each specialist replies once.
   Retry a missing hand-off once, then record BLOCKED and continue independent
   tasks. Do not ping a blocked dependency every hour without changed evidence.
5. Reconcile the board, proposal register and actual runtime outcomes. Save one
   synthesis plus updated task states. Notify the user only for a meaningful
   finding, completed improvement, failure or required action; otherwise remain
   quiet while retaining completion evidence.

At 09:15 daily, or the first later invocation when that local date is unfinished,
request one X setup investigation and one primary-source check. Require a source,
mechanism, existing-strategy mapping and falsification condition. Deduplicate by
local date, source/version and task ID. Record DONE or DONE_WITH_GAPS after the
bounded pass; an access block must not cause endless same-day research retries.
Carry unresolved access needs separately. A delayed first run can execute this
pass without creating another routine.

New-position discovery means examining the existing strategy's broader public
radar universe and explaining why candidates pass or fail. The deterministic
engine alone promotes/adopts eligible candidates and opens paper positions under
its current rules. A research post, specialist preference or empty portfolio slot
never becomes extra entry authority. Preserve 12–200 grids, 5x, the strict >1%
per-pair margin-return floor after two 0.06% fills, confirmed chart bounds, entry
splits, five-position and four-per-direction caps and all existing risk gates.

## Needs backlog and orchestrator visibility

Keep one board with task_id, owner, status, source_asof, strategy_version,
evidence_uri, dependency, blocker and updated_at. Add need_type, expected_result,
next_check_trigger and acceptance_evidence for needs discovered from operation.
Task states are OPEN, ASSIGNED, IN_PROGRESS, BLOCKED, DONE or DONE_WITH_GAPS;
UNKNOWN records missing reported state. These are separate from proposal states.

| Initial need | Owner | Initial evidence state | Completion evidence |
| --- | --- | --- | --- |
| Current v2 schedule and daily dispatch | Grid Desk Lead | Saved prompt verified; unattended run pending | Saved prompt plus actual unattended run history. |
| Durable cross-room board and restart recovery | Grid Desk Lead | Write/read verified; restart untested | Persisted board readback; unfinished task resumed and completed task deduplicated. |
| RD-002 market/reviewer packet | Operations/data steward | Verified | Attributed read-only packet with per-source as-of, coverage, units and version. |
| Stale SmartTrading ingestion / zmarty-axi failure | Operations/data steward | Known source gap | Root-cause evidence and a scoped repair work item; fresh per-table/per-symbol checks if a repair is later implemented. |
| Public X setup access | X Setup Researcher | ACCESS_BLOCKED | Original accessible post or explicit failed retrieval with alternative evidence, without invented content. |
| New candidate evidence and admission reasons | Data & Structure | Must be checked each relevant radar version | Candidate/source IDs, engine eligibility evidence or precise missing field. |
| Rule outcome reconciliation | Strategy Manager | Integration acceptance pending | Generated evaluation, real application/no-change result and matching active state. |

The Lead owns every assignment's follow-through, not omniscient knowledge of
other agents. A role acknowledgment does not prove task completion. Resolve
conflicting data through evidence, and leave blocked work attributed rather than
silently dropping it. Create new needs automatically when missing data, recurring
failure, unclear accounting or a useful within-strategy capability is discovered.

Authorized research, public reads, evidence review, task assignment and backlog
maintenance proceed without asking “should I continue?” A need that requires
implementation becomes a concrete scoped work item with owner, tests and
acceptance evidence. Research assistants must not invent credentials, execute
runtime edits or imply the task board itself grants missing write authority.

## Results-to-improvement lifecycle

Use `OBSERVED -> HYPOTHESIS -> TEST_READY -> SHADOW -> ELIGIBLE_TIER1 / DEFERRED /
REJECTED -> APPLIED_VERIFIED / ROLLED_BACK`. Attach cohort/window, net costs,
coverage, strategy version, expected benefit and a falsification criterion.
Separate correlated observations and completed grids from independent closed
outcomes. A result-driven system must be able to reject its own hypothesis.

For existing implemented tier-1 rules, preserve the current 07:00 deterministic
evaluator and applier, exact rule-specific sample/coverage/benefit gates and
three-application daily cap. Do not replace their generated evidence with LLM
prose. The learned seven-day symbol cooldown differs from ordinary six-hour
post-close cooldown. A dry-run can publish status counts while leaving active
rules unchanged; APPLIED_VERIFIED needs an actual non-dry-run result and matching
active state. NO_CHANGE with a reason is a valid observed execution outcome.

Novel rules become scoped implementation/test needs and remain SHADOW until the
necessary reviewed implementation, meaningful tests and forward paper evidence
exist. Define baseline, comparable windows, fee/funding accounting, success and
failure thresholds, and rollback evidence before promotion. The Manager cannot
rename a new idea “tier 1” to make it executable. Broader implementation ownership
and release integration must be explicit in the work item; this documentation
neither installs such an integration nor expands the current writer's scope.

## Evidence required to claim completion

Demonstrate one scheduled hourly round, one daily research pass or its legitimate
catch-up, one recoverable board transition, one completed steward hand-off or
honest partial failure, and one needs item followed through to its acceptance
result. Verify that the Lead knows all assigned tasks and labels unreported work
UNKNOWN. Verify existing deterministic execution continues independently and
that the Manager accurately distinguishes proposed, deferred and applied rules.

Until these artifacts are observed, report the system as configured with the
specific integration gaps above. No extra routine, code, dependency, deployment,
source mutation or strategy change is included in this design document.


## Subsequent acceptance updates

The gaps table above records the initial review, not current completion claims.
The current saved native routine was inspected after the autonomy update; it
checks due research/backlog before quiet exit and scans core/bench separately.
RD-002 delivered and verified all five open symbols. Native board.json was created
and independently read; actual restart recovery is still untested. SB-001 native
steward read completed01:02:27UTC and correctly labeled the source historical.
See [version2 observations](paper-team-v2.md) for source and exact times.

An executable [research-request bridge](paper-team-bridge.md) now closes the
Manager-request/evaluator-result path for the three existing hypotheses. Initial
real replay returned a gated deferral. It does not turn the Manager into a rule
writer or validate the separate paired-forward-experiment ideas. Final native
acknowledgment, schedule invocation and release checks are recorded separately.


The installed Codex support heartbeat now has two daily phases:08:55 sanitized
Supabase feed and10:55 one evidence-backed engineering need. The latter works in
an isolated Codex checkout, runs required tests and prepares the relevant PR,
then writes engineering-status.json for native steward/Lead intake. It cannot
change production strategy/state or merge/deploy its own result. Novel-rule
implementations remain shadow work until measured forward evidence and review.
One heartbeat hosts both phases because the app permits only one per task;
there is no duplicate native routine. First scheduled phase execution is unproven.
