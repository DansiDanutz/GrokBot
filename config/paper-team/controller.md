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

At08:15 run the bounded [Supabase phase](supabase-data-bridge.md) once per date.
At10:15 process one supported engineering need with resolved dependencies in an
isolated Codex checkout; run required tests, prepare the relevant PR and publish
engineering-status.json. Preserve previous results. New access, paid plans,
production strategy/data edits and auto-merge/deploy are outside that phase.

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
Other bots and existing legacy routines remain outside this controller.

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
