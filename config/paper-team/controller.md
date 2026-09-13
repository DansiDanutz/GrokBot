# Current paper-team controller

Authoritative scheduling configuration after the September13 acceptance checks:

| Component | State and cadence |
| --- | --- |
| Codex heartbeat `paper-team-supabase-evidence-bridge` | ACTIVE, hourly:15 Europe/Bucharest. Name: Paper team — hourly controller and improvements. |
| Native `paper-grid-team-hourly-evidence-review` | PAUSED, retained cadence hourly:15. Its instructions remain Grid Desk Lead's playbook. |
| Supabase data phase | First due08:15 invocation per local date, with delayed catch-up. |
| Native X/primary-source research | First due09:15 controller cycle per local date, with delayed catch-up. |
| Codex engineering phase | First due10:15 invocation per local date, at most one supported need. |
| Existing trading engine and07:00 rule pipeline | Independent, unchanged execution ownership. |

This supersedes the earlier native timer and08:55/10:55 support-only schedule.
There is one hourly controller, not two competing timers. Do not enable the
native timer unless verified repair and a coordinated ownership switch disable
the external dispatch first.

## Evidence for the handoff

The native timer was Active but UI and supported server automation_status said
never run after its intended04:15 invocation. The same routine was re-armed at
04:19:32 for a temporary04:22 test, with unchanged instructions and no manual
review. UI still showed no runs at04:24. During handoff the Lead subsequently
reported a late `running now` status timestamped01:24:19UTC, while UI history
remained empty. That is an in-flight signal, not proof of completed scheduled
collaboration. Do not claim no native execution ever started or that the platform
was repaired. Cadence restored:15 and native timer pause confirmed04:24:50;
steward persisted the scheduler handoff at01:26:02UTC.

The Codex heartbeat was updated in place at approximately01:26UTC. Its first
unattended dispatch is still unproven; record it only when actually observed.
The native evaluator and cross-room response path were manually verified before
this timing handoff. Machine and app availability remain execution dependencies.

## Each hourly controller invocation

Recover controller-state.json, board.json and referenced result receipts under
`~/Sandbox/grokbot/team-evidence/` and `team-experiments/`. Use a unique local
date/hour cycle ID with separate UTC observation time. First reconcile any
previously dispatched cycle. Do not resend a completed or still-running cycle.
Control state is separate from the steward-owned native board.

At or after08:15 run the bounded [Supabase phase](supabase-data-bridge.md) once per date.
At or after10:15 process one supported engineering need with resolved dependencies in an
isolated Codex checkout; run required tests, prepare the relevant PR and publish
engineering-status.json. Preserve previous results. New access, paid plans,
production strategy/data edits and auto-merge/deploy are outside that phase.

On every wake, derive each phase's due time in Europe/Bucharest and persist a
record under `daily_phase_outcomes[local_date][phase]`: `due_at`, `status`,
`request_id`, `started_at`, `updated_at`, `completed_at`, `receipt`, and `blocker`.
Use PENDING, RUNNING, BLOCKED, COMPLETED, or NO_ELIGIBLE_NEED. At the first later
wake after a missed due time, run each unfinished due phase once in data →
research → engineering order. Reconcile RUNNING work before any retry and reuse
its request ID; a BLOCKED phase retries only when its dependency changes. A phase
is COMPLETED only with its actual artifact/result receipt (and steward readback
for native work). NO_ELIGIBLE_NEED requires a recorded eligibility check. Do not
mark a missed phase complete or repeat a completed phase. Mark prior-date missed
phases MISSED with a reason; refresh the current date instead of replaying stale
research or engineering backlogs. These records are controller-owned, not board
writes. Configuration or this acceptance check is not an unattended run.

Use supported computer-use tools to open the existing Grid Desk Lead chat and
send one concise cue, for example:

> Controller cycle CTRL-YYYYMMDD-HH, scheduled external invocation, observed UTC
> [time]. Native timer remains paused. Execute one due cycle of your saved
> paper-team playbook: recover the board and previous receipts, review current
> autopilot plus separate radar core/bench candidates, process due research/needs,
> delegate necessary evidence checks, then persist/read back the board. Do not
> repeat completed evaluations. Report actual completion or a named blocker.

Never claim a manually sent acceptance cue was a scheduled invocation. Do not
read app databases, use undocumented APIs, acquire new connections or add agents.
The newly configured Paper Desk Secretary receives material user-facing hand-offs
through the Office room; it is not a scheduler. Unrelated bots and existing
legacy routines remain outside this controller.

The Lead routes all research and specialist work. The existing operations bot
submits validated seven-field requests to the installed bridge; it never invokes
the applier. The bridge owns result receipts. Logical evaluation dedup uses
review_date, rule_id, strategy_revision and actual evidence_hashes, with max one
new evaluation per hourly round and three per local date. Unknown rule IDs become
NEEDS_IMPLEMENTATION; applied:false never becomes an application claim.

Inspect actual replies and the steward's board acknowledgment with waits no
longer than30seconds, bounded to five minutes total. Retry one missing handoff;
do not duplicate the entire cycle or evaluation. If still incomplete, persist
PENDING/BLOCKED plus the dispatched ID so the next wake reconciles it. Distinguish
UI unavailability, active prior work, stale source and missing board persistence.
Notify only for meaningful findings, completed improvements, new failures or
required action. Quiet notification does not suppress due work.

## Durable evidence

The controller atomically maintains controller-state.json and daily phase markers.
The native steward maintains board.json, preserving prior versions and returning
readback. Operator config/executable and generated experiment receipts are not
editable by research roles. A material round is durably complete only with a
board acknowledgment; one failed retry becomes BOARD_PERSIST_BLOCKED.

Keep task state, canonical advisory HOLD/REVIEW/ESCALATE, proposal stage, and
observed engine effect separate. Existing rules are OBSERVED unless real
application and post-change evidence exist. Preserve every current strategy and
risk gate in the standing contract; results may justify tested improvements,
never fabricated performance or copied social entries.

_Last verified: 2026-09-13_


## Secretary, range assurance and final-response routing

The participant count is ten; the two original six-member rooms are unchanged.
Office hand-offs (Secretary, Lead, existing steward) and private board readback
are observed; the native steward acknowledgment and Lead closure are verified. Acceptance includes the native steward acknowledgment and Lead closure recorded below.

The single Codex heartbeat remains hourly :15 and the native timer stays PAUSED.
Lead owns operational orchestration; the existing steward alone writes board.json;
Paper Desk Secretary is the main user contact. After the board write/readback
receipt, Lead sends Secretary a concise material result and any unresolved user
request, with task/request ID, owner, source_asof, assigned_at, updated_at, actual
status, receipt/version and final_response status/reference. Inspect that hand-off
and the final response before marking user-request closure. A missing report is
UNKNOWN; a blocked receipt is BOARD_PERSIST_BLOCKED, not DONE. Do not duplicate a
final response or send unchanged hourly summaries to the Secretary.

Controller review must preserve the distinction between the mandated range/fee
policy and issue #52 implementation status. Complete-bar freshness/history,
range dossiers, 0.06% both-side futures-bot fees and strict observed boundary-exit
exception handling are DEPLOYED_VERIFIED at production f02703 following the
explicitly requested controlled release. Instruction edits alone are not that evidence. New inactive rotation
is NOT_IMPLEMENTED and remains a scoped need/shadow evaluation, with validated
replacement/net-cost benefit and minimum-hold conditions. No assistant gains
runtime write or trade authority from the user's request for an explanation.


## Office acceptance evidence — 2026-09-13

Parent-verified evidence: Secretary QA at 01:53:12 UTC; Lead relay at 01:53:53 UTC;
steward Office board entry written at 01:54:21 UTC, native acknowledgment at
01:54:41 UTC, board readback at 01:55:00 UTC and Lead closure at 01:55:22 UTC.
The Office hand-off is verified complete. Current-cost dossier QA02 was sent at
01:57:44 UTC; Secretary returned the five-position table at 01:58:50 UTC and the
steward persisted its result at 01:59:05 UTC (native acknowledgment 01:59:23 UTC).
This verifies the user-request → Lead → steward → Secretary → record path.


## Verified range-assurance release — 2026-09-13

PR #53 merged to `f02703afcb57278d96b9c99063663cdb2f22a2b2`, the verified production
HEAD. Tested head: `6062d392a8680c67f5dd0ae4d85facc6375b3fec`. Independent review,
Mac/Linux CI, 38 Node + 335 paper + 422 trader tests (795 total), and a 270-file
secret scan passed. The controlled same-label writer/dashboard cutover preserved
prior bot IDs, ledger start and the unrelated dirty production file.

At 02:18:50 UTC the parent verified HTTP 200 for local health/autopilot/radar,
a fresh heartbeat, kucoin_ok=true, recovery_pending=false, zero pending funding
and recovery items, and archive status COMPLETE. New entries require complete
168-hour history proof, 0.06% both-side bot fees, maximum viable grid selection,
range dossiers, durable SHA archives and cost scenarios. This is source-time
acceptance evidence, not an indefinite guarantee of live health.

Legacy dossiers currently remain MISSING; do not manufacture historical entry
proof. Invalid legacy pair-floor handling uses PROFILE_UPDATE with the existing
non-risk hold. Post-rollout observation included a held legacy position under
that rule. Boundary touch/breach risk exits bypass that non-risk hold. Preserve
historical fee epochs and distinguish these two exit reasons.

The bridge was explicitly repinned to f02703 and context verified at 02:18:06 UTC;
its installed worker was unchanged. Old 724-source evaluation receipts remain
historical. Sanitized deployment-range-assurance.json and engineering-status.json
were prepared under team-evidence and the heartbeat knows this release.
The steward read the new release artifacts and persisted the implementation
closures at 02:24:22 UTC. Secretary returned the corrected direct-chat answer at
02:24:42 UTC. Native release handoff is verified; this is not a learned-rule win.

Rotation remains NEEDS_IMPLEMENTATION, heatmap access remains UNAVAILABLE and
the first unattended Codex-controller completion remains unverified. This
explicitly requested release does not widen the scheduled 10:15 engineering
phase: its no-auto-deploy scope and controlled repin ownership remain unchanged.
