# Autonomous paper team: current operation

Grid Desk Lead coordinates the trading and research rooms. Paper Desk Secretary
is the main user contact, linked to Lead and the existing records steward in
Paper Desk Office: ten participants overall. The existing deterministic engine discovers eligible
positions and manages paper trading; its existing07:00 pipeline alone writes
supported learned rules. The team supplies research, technical interpretation,
evaluation, outcome review and an owned needs backlog.

## One controller

The [Codex controller](../config/paper-team/controller.md) is ACTIVE hourly:15
Europe/Bucharest. It sends one identified cycle cue to the existing Lead and
performs daily08:15 data and10:15 engineering phases. Daily X/primary-source
research is due09:15 within the Lead's playbook, or the first later catch-up that
date. Every daily phase is deduplicated.

The native hourly timer is PAUSED, with its :15 cadence and instructions retained.
Its04:15 scheduled invocation could not be verified. A re-armed04:22 test still
showed no UI history at04:24; the Lead subsequently reported a late running signal
at01:24:19UTC. That signal is not proof of completed scheduled collaboration.
The handoff prevents competing timers while keeping the platform issue visible.
Do not claim that no native run ever started or that its scheduler was repaired.
The first unattended Codex dispatch remains to be observed. Manual workflow
acceptance and configured scheduling are separate facts.

## The hourly cycle

1. Controller recovers its control state and the steward-owned board. It reconciles
   a pending prior cycle before sending another cue. Completed/working cycle IDs
   are not redispatched.
2. Lead recovers board, overdue needs, daily research due state and source health
   before any quiet exit. It checks current autopilot and radar core/bench as
   separate candidate sets, not substitutes for open_bots.
3. Data & Structure identifies candidate eligibility and rejection evidence.
   Technical Interpreter verifies source scope, then interprets joint market and
   derivatives evidence. Risk checks existing gates; Performance evaluates net
   after costs. Manager tracks actual rule outcomes and supported hypotheses.
4. X Setup Researcher and Research Scout deliver one bounded item each on the due
   daily pass. A source block is attributed and does not stop independent work.
   Claims need original source, mechanism, strategy mapping and falsification.
5. A supported hypothesis can enter the [request bridge](paper-team-bridge.md).
   Existing steward submits the exact seven-field request; the worker recomputes
   evidence using the pinned daily evaluator. Maximum one new evaluation per
   hourly round and three per local date. Logical dedup uses review_date, rule_id,
   strategy_revision and actual evidence_hashes; request ID is a reference.
6. Lead reconciles actual specialist replies, proposal stage and engine events.
   Steward atomically preserves, updates and reads back board.json before durable
   completion. After one failed retry, record BOARD_PERSIST_BLOCKED and retain
   the true findings without claiming restart-safe completion.

The controller observes replies and board acknowledgment for at most five minutes
using short waits. Incomplete work stays PENDING/BLOCKED with its dispatched ID;
the next wake reconciles it. Quiet notifications do not suppress due work.

## New positions and improvements

New-position discovery uses the current strategy's public radar universe and its
existing admission evidence. The deterministic engine alone opens eligible paper
positions. Zero eligible candidates is valid. Preserve confirmed S/R,12–200 grids,
5x, strictly>1% complete-pair return on allocated margin after both0.06% fills,
entry splits, max five positions, max four in one direction and all existing
risk, reserve, activity, sizing, funding and exit controls.

Use OBSERVED → HYPOTHESIS → TEST_READY → SHADOW → eligible existing tier1 /
DEFERRED / REJECTED → APPLIED_VERIFIED / ROLLED_BACK as evidence states. The
sandbox worker never applies a rule. A qualifying planner result is SHADOW here;
a missing gate remains DEFERRED. New identifiers become NEEDS_IMPLEMENTATION,
not executable model-authored code or a bypass around the existing applier.

Daily engineering selects one OPEN need with resolved dependencies, measurable
acceptance and an existing issue/PR ownership check. It works in an isolated Codex
checkout, uses existing dependencies, runs repository checks and reports actual
commit/PR/CI evidence. No production deployment, auto-merge or new rule writer is
introduced. Novel rules can gain an offline implementation and shadow experiment;
forward evidence and review remain necessary before a binding change.

## State and ownership

| Artifact | Writer and purpose |
| --- | --- |
| team-evidence/controller-state.json | Codex: hourly dispatch and daily phase receipts, duplicate prevention. |
| team-evidence/board.json | Existing steward on Lead instruction: assigned owners, states, dependencies, acceptance and actual result references; preserve prior versions. |
| team-evidence/supabase-market-context.json | Codex data phase: sanitized market archive packet, real source timestamps, coverage and historical status. |
| team-evidence/engineering-status.json | Codex engineering phase: one need's tested result, PR, blocker and next action. |
| team-evidence/operator/ | Operator-managed installed executable/config; native roles do not edit it. |
| team-experiments/inbox/ | Native steward: new validated requests only. |
| team-experiments/results/ | Verified worker: immutable generated reports, metrics, source/input hashes and acknowledgments. |
| Production trading state and learned rules | Existing engine and existing daily applier only. |

Lead tracks assigned work; unreported private activity is UNKNOWN. Keep task
state, canonical advisory HOLD/REVIEW/ESCALATE, rule lifecycle and engine effect
separate. A PR is READY_FOR_REVIEW, not APPLIED. Native and Codex control records
are same-user workflow boundaries, not OS security isolation.

## Verification and remaining needs

RD-002 correctly selected all five open positions and passed technical scope
validation. SB-001 verified sanitized Supabase file transport, preserving stale
DOT-only data and four missing symbols. The installed worker and native request
NATIVE-TREND-20260913-01 generated DEFERRED/applied:false from real September12
evidence. Risk, Performance and Lead acknowledged it; completed board and actual
evidence hashes were read back at01:16:35UTC.

The native board recorded scheduler handoff at01:26:02UTC. Initial code passed
677tests and macOS/Ubuntu CI; cached false-application and import-shadowing review
findings were fixed and independently rechecked. Final receipt evidence belongs
in the [living audit](paper-team-audit.html), with latest-head CI attribution.

Open needs: first unattended external dispatch, native scheduler observability,
actual app-restart recovery, original X grid-source access, stale Supabase
coverage, Zmarty health503, interval-matched funding reconciliation, original
entry-split evidence and unimplemented forward experiments. CoinGlass heatmap
entitlement and positive trading expectancy are not claimed. Continue authorized
independent work and attribute blocks; do not invent data or ask for routine
permission to proceed.

_Last verified: 2026-09-13_


## Range and cost release

The [range-assurance release](paper-team-range-assurance.md) is deployed as
`f02703afcb57278d96b9c99063663cdb2f22a2b2` (PR53). It requires entry provenance,
official bot-fee economics and durable dossiers, preserves observed boundary
closes through funding/recovery failures, and defers learning on unresolved
accounting. Legacy layouts failing the fee floor use the existing non-risk
PROFILE_UPDATE hold. Secretary direct-contact and release readback are verified.
This explicit user-requested release does not widen the scheduled engineering
phase. First unattended controller completion remains unverified.
