# Bounded autonomous research evaluation

The native Strategy Manager can submit a structured hypothesis request through
existing Dan’s Senior Developer. The bridge runs the existing deterministic daily
reviewer against paper data and returns generated evidence. It never calls the
rule applier, changes the trading engine, or interprets research text as code.

## Installed boundaries

The operator pins the deployed source revision and input paths in
`~/Sandbox/grokbot/team-evidence/operator/bridge-config.json`. The executable is
copied alongside it as `paper-team-bridge.py`. These are operator-managed; native
roles may not edit them. Native requests belong only in
`~/Sandbox/grokbot/team-experiments/inbox/`. Generated results are reserved for
the bridge under `team-experiments/results/`. Team boards remain in
`team-evidence/board.json`, outside the experiment inbox.

These are same-user workflow boundaries, not OS isolation against a malicious
process with that user's full filesystem access. Runtime inputs remain read-only.
The worker checks pinned source, request freshness, evidence hashes and output
integrity; unknown identifiers become needs, never arbitrary Python or shell.

## Request and acknowledgment

Run the installed worker with its immutable operator config:

```sh
python3 /Users/davidai/Sandbox/grokbot/team-evidence/operator/paper-team-bridge.py --config /Users/davidai/Sandbox/grokbot/team-evidence/operator/bridge-config.json context
```

Copy the context's version, strategy_revision, created_at, review_date and
evidence_hashes into a new request. Add a unique request_id and rule_id; remove
the informational allowed_rule_ids field. Write once to `inbox/REQUEST-ID.json`.
Only these seven fields are accepted. No command, source code, output path,
model-supplied performance or gate override is allowed in the request.

The Manager keeps source citations, mechanism, falsification condition, cohort
and expectations in its separate board/specification. Those claims do not become
the evaluator's observed metrics. Evaluate a completed day within the trailing
30-day window. Context expires in an hour or when pinned evidence changes.

```sh
python3 /Users/davidai/Sandbox/grokbot/team-evidence/operator/paper-team-bridge.py --config /Users/davidai/Sandbox/grokbot/team-evidence/operator/bridge-config.json run --request REQUEST-ID
```

Supported IDs are `min_hold_hours_before_non_risk_close`,
`require_trend_alignment`, and `symbol_cooldowns`. A syntactically valid new rule
returns NEEDS_IMPLEMENTATION without running an evaluator. Malformed, stale,
version-mismatched or changed-evidence requests reject. An identical request ID
returns its validated prior acknowledgment without recomputing; changed content
under that ID rejects. A crashed reserved directory requires inspection and a
new ID, not deletion or blind replay.

For a supported rule the worker freezes state/rules, records source hashes, runs
only `trader.review.daily`, then reads the pure `rules.plan_changes` result into
metrics.json. It preserves review.md, proposals.json and result.json with hashes.
A qualifying plan is SHADOW here; an unmet gate is DEFERRED. Every acknowledgment
has applied:false. REJECTED/BLOCKED are failures to resolve, not successful tests.
Results do not enter the live applier's input path.

## Routine integration

On a due external controller cue (native timer paused), Lead recovers the board and asks Manager for at most one new bounded
request per completed review date/rule/evidence version. Steward obtains fresh
context, submits it, runs the worker once and returns the acknowledgment plus
metrics to Manager, Performance, Risk and Lead. Maximum one evaluation per hourly
round and three distinct evaluations per local date; overdue research proceeds
before the routine's quiet branch. These workload caps live in the orchestrator's
routine, while the worker enforces locking, 180-second process timeout and exact-ID
deduplication. A failed handoff gets one retry; source/version blocks wait for
operator maintenance. Do not automatically repin a changed production revision.

Lead persists completed request IDs and needs in the board. Forward paired tests
and new rules remain separate implementation needs until code and evidence exist.
The existing 07:00 evaluator/applier remains the sole automatic tier-1 writer;
the team reads its real results rather than copying sandbox JSON into that path.

## Initial real acceptance

At 2026-09-13T01:00:50Z, request ACCEPT-TREND-20260913-01 evaluated September12
against deployed source 7244d26174fac1ceb4dcab74c41ab105b6e257df. The generated
result was DEFERRED, applied:false: 8 opened, 6 closed, 17 cumulative closes;
trend evidence had 4 directional opens versus minimum5 and flagged FHEUSDTM
coverage below95%. This is a real retrospective rule evaluation, not a paired
forward experiment or evidence of improved returns.

Native end-to-end acknowledgment and future scheduled runs must be recorded
separately from this operator-run acceptance. A configured routine is not run
history. Production rules and review-status hashes stayed unchanged.

_Last verified: 2026-09-13_


After independent review, cached receipt checks were strengthened and evaluator
imports were moved to an isolated verified source export. All24bridge tests pass.
Post-fix real request ACCEPT-TREND-20260913-02 completed01:07:32.926002UTC with
DEFERRED/applied:false and the same unmet gates. Installed duplicate-read
validation returned that original receipt without another evaluation. Pre-fix
receipts are preserved as historical evidence and intentionally fail the new
cache-integrity checks; use a new request ID for any justified fresh evaluation.


Native operations independently submitted NATIVE-TREND-20260913-01. Its generated
result completed2026-09-13T01:08:51.766807Z with DEFERRED/applied:false. This verifies
native execution of the installed worker; specialist acknowledgment and scheduled
routine execution remain separate acceptance facts.


## Explicit release repin — 2026-09-13

After PR #53's controlled production cutover, the operator explicitly repinned
bridge-config.json to `f02703afcb57278d96b9c99063663cdb2f22a2b2`. Context was verified
at 02:18:06 UTC. The installed worker remained unchanged; this was a source-pin
maintenance action, not a new worker or widened execution capability. Initial
7244d26174fac1ceb4dcab74c41ab105b6e257df receipts above retain their historical
source/version and outcomes; the repin does not relabel them as f02703 evaluations.

The sanitized deployment packet and engineering-status.json are available under
team-evidence, and the existing heartbeat prompt knows the release. Native
Secretary acknowledgment of this release is verified: steward record02:24:22UTC,
Secretary answer02:24:42UTC. The first
unattended Codex controller completion is also unverified. This explicit
user-requested deployment/repin does not authorize the scheduled 10:15 engineering
phase to deploy or repin automatically. The bridge remains a shadow evaluator;
it never invokes the live applier or writes strategy state.
