# Team orchestration

Every participant is linked, receives work when its trigger fires, and is
reported idle when it does not answer. The doctor knows when the team itself has
stalled.

This replaces a controller that could stop without anyone noticing. On
2026-09-13 the hourly :15 Codex heartbeat ran out of tokens,
`controller-state.json` froze at cycle `CTRL-20260913-05` RUNNING at 02:34 UTC,
and the 08:15, 09:15 and 10:15 daily phases never ran. Ten participants waited
for a cue that could not arrive, and nothing in the system said so. The
controller now runs from launchd (`config/launchd/com.danslab.trader-team-controller.plist.example`),
publishes what it dispatched, and the doctor's seventh check fails when it stops.

Run one cycle:

```sh
python3 -m trader.team [--dry-run] [--now-ms MS] [--evidence-dir DIR] \
    [--experiments-dir DIR] [--runtime-dir DIR] [--radar FILE] [--database FILE]
```

`--dry-run` prints the dispatches it would create and writes nothing. A real run
writes three files atomically: `team-evidence/controller-state.json` (schema 2),
`team-evidence/dispatch.json` (the local board the doctor reads) and
`<runtime-dir>/team.json` (the sanitized public file the native bots poll). It
never writes `board.json`: existing Dan's Senior Developer alone owns that.

## The roster

Native bots read only public URLs. The steward reads this machine. Deterministic
members are jobs, and their cadence is their trigger. `Max idle` is how long
silence from that participant is still acceptable before the controller calls it
idle and the doctor warns.

| Role | Kind | Room(s) | Trigger events | Max idle | Installed |
| --- | --- | --- | --- | --- | --- |
| Grid Desk Lead | native | Paper Grid Trading Team, Paper Grid Research & Data, Paper Desk Office | `ROLE_IDLE`, `CONTROLLER_RESUMED`, `SYNTHESIS` | 24.0h | yes |
| Data & Structure | native | Paper Grid Trading Team | `RADAR_SCAN` | 24.0h | yes |
| Risk Sentinel | native | Paper Grid Trading Team | `DOCTOR_FAIL`, `DOCTOR_RECOVERED`, `ENTRIES_STALLED`, `STRUCTURE_BLACKOUT` | 24.0h | yes |
| Performance Analyst | native | Paper Grid Trading Team | `BOT_CLOSED`, `COUNTERFACTUAL_READY` | 24.0h | yes |
| Research Scout | native | Paper Grid Research & Data | `RESEARCH_DUE` | 24.0h | yes |
| X Setup Researcher | native | Paper Grid Research & Data | `RESEARCH_DUE` | 24.0h | yes |
| Strategy Manager | native | Paper Grid Trading Team, Paper Grid Research & Data | `LEARNER_DEFERRED`, `LEARNER_APPLIED`, `COUNTERFACTUAL_READY`, `ENGINEERING_DUE` | 24.0h | yes |
| Technical Interpreter | native | Paper Grid Trading Team, Paper Grid Research & Data | `BOT_OPENED`, `LIQ_CLUSTERS_READY` | 24.0h | yes |
| Discovery Auditor | native | Paper Desk Office | `DISCOVERY` | 24.0h | **no - Dan must add it** |
| Dan's Senior Developer | steward | Paper Grid Research & Data, Paper Desk Office | `DOCTOR_FAIL`, `ENTRIES_STALLED`, `STRUCTURE_BLACKOUT`, `DATA_PHASE_DUE`, `CONTROLLER_RESUMED` | 6.0h | yes |
| Paper Desk Secretary | secretary | Paper Desk Office | `FINAL_RESPONSE` | 24.0h | yes |
| Market data collector | deterministic | not in a room | its own cadence | 1.0h | yes |
| Radar | deterministic | not in a room | `RADAR_SCAN` | 2.0h | yes |
| Autopilot | deterministic | not in a room | `BOT_OPENED`, `BOT_CLOSED`, `ENTRIES_STALLED`, `STRUCTURE_BLACKOUT` | 0.1h | yes |
| Publisher | deterministic | not in a room | its own cadence | 0.5h | yes |
| CoinGlass history | deterministic | not in a room | its own cadence | 3.0h | yes |
| Liquidation clusters | deterministic | not in a room | `LIQ_CLUSTERS_READY` | 3.0h | yes |
| Daily review | deterministic | not in a room | `LEARNER_DEFERRED`, `LEARNER_APPLIED` | 26.0h | yes |
| Counterfactual replay | deterministic | not in a room | `COUNTERFACTUAL_READY` | 26.0h | yes |
| Doctor | deterministic | not in a room | `DOCTOR_FAIL`, `DOCTOR_RECOVERED` | 1.0h | yes |
| Team controller | deterministic | not in a room | `ROLE_IDLE`, `CONTROLLER_RESUMED`, `DATA_PHASE_DUE`, `RESEARCH_DUE`, `ENGINEERING_DUE` | 2.0h | yes |

## Event to role

Events come from the doctor report, `autopilot.json`, the event log, the daily
review status, the counterfactual and liquidation-cluster artifacts, the clock
and the controller's own state. The vocabulary is closed: `DOCTOR_FAIL`,
`DOCTOR_RECOVERED`, `RADAR_SCAN`, `BOT_OPENED`, `BOT_CLOSED`, `ENTRIES_STALLED`,
`STRUCTURE_BLACKOUT`, `LEARNER_DEFERRED`, `LEARNER_APPLIED`,
`COUNTERFACTUAL_READY`, `LIQ_CLUSTERS_READY`, `RESEARCH_DUE`, `DATA_PHASE_DUE`,
`ENGINEERING_DUE`, `ROLE_IDLE`, `CONTROLLER_RESUMED`. Markers persisted in the
controller state make each one fire once: a fault lasting nine hours is one
dispatch, not nine.

The table is read top to bottom when the open-dispatch cap bites, so the events
that cannot wait are routed first.

| Event | Roles dispatched | Also |
| --- | --- | --- |
| `DOCTOR_FAIL` | Risk Sentinel, Dan's Senior Developer | Lead synthesis, Secretary final response |
| `STRUCTURE_BLACKOUT` | Dan's Senior Developer, Risk Sentinel | Lead synthesis |
| `ENTRIES_STALLED` | Dan's Senior Developer, Risk Sentinel | Lead synthesis |
| `CONTROLLER_RESUMED` | Grid Desk Lead, Dan's Senior Developer | Lead synthesis |
| `DATA_PHASE_DUE` | Dan's Senior Developer | Secretary final response |
| `RESEARCH_DUE` | X Setup Researcher, Research Scout | Secretary final response |
| `ENGINEERING_DUE` | Strategy Manager | controller runs the operator bridge first |
| `ROLE_IDLE` | Grid Desk Lead | Lead synthesis |
| `LEARNER_APPLIED` | Strategy Manager | Lead synthesis |
| `LEARNER_DEFERRED` | Strategy Manager | Lead synthesis |
| `COUNTERFACTUAL_READY` | Strategy Manager, Performance Analyst | Lead synthesis |
| `LIQ_CLUSTERS_READY` | Technical Interpreter | Lead synthesis |
| `BOT_CLOSED` | Performance Analyst | Secretary final response |
| `BOT_OPENED` | Technical Interpreter | Lead synthesis |
| `RADAR_SCAN` | Data & Structure | Lead synthesis |
| `DOCTOR_RECOVERED` | Risk Sentinel | Lead synthesis |

Standing dispatches, outside the event stream:

- **SYNTHESIS** — Grid Desk Lead, once in any cycle that dispatched anything.
- **FINAL_RESPONSE** — Paper Desk Secretary, only when a user-facing outcome
  exists: a close, a doctor failure or a phase completion.
- **DISCOVERY** — Discovery Auditor, once a day at 10:30 Europe/Bucharest, with
  the gaps the controller can compute itself: roles not installed, doctor checks
  reading `unknown`, data sources older than their cadence, blocked dispatches.

`RADAR_SCAN` reaches Data & Structure only when the candidate set actually
changed; a repeat scan with the same candidates is not work.

## Dispatch and receipt

A dispatch is one question to one role:

| Field | Meaning |
| --- | --- |
| `dispatch_id` | `D-YYYYMMDD-HH-<role>-<EVENT>-<n>`, deterministic per cycle. |
| `role` / `role_name` | The snake_case id and the exact native bot name. |
| `room` | Where the answer is expected. |
| `event` | The event that raised it, or a standing dispatch name. |
| `payload` | Numbers and strings only. Never a local path, host or secret. |
| `instruction` | Short text from the per-event template. |
| `created_at` / `due_at` | UTC. Due is created + 2h; daily phases get 3h. |
| `status` | `PENDING`, `DONE`, `BLOCKED` or `NOT_INSTALLED`. |

A receipt is the steward's proof that the answer exists. It is written to
`team-evidence/receipts/<dispatch_id>.json`:

| Field | Meaning |
| --- | --- |
| `dispatch_id` | The dispatch being answered. |
| `role` | The role that answered, by native name or id. |
| `answered_at` | UTC ISO-8601 time the answer was posted. |
| `room` | The room the answer was posted in. |
| `summary` | At most 600 characters of what was said. |

Reconciliation on the next cycle: a receipt makes the dispatch `DONE`; for the
engineering phase, `team-experiments/results/<request_id>/result.json` does the
same. A `PENDING` dispatch past its `due_at` becomes `BLOCKED` and raises
`ROLE_IDLE` for that role on the following cycle. A dispatch addressed to a role
that is not installed becomes `NOT_INSTALLED`, never `BLOCKED`: the gap is Dan's
to close, not the role's to answer.

At most 12 dispatches are open at once, and the same `(role, event, key)` is
never dispatched twice while one is still open.

## Daily phases

Due 08:15 (data), 09:15 (research) and 10:15 (engineering), Europe/Bucharest,
once per local date, with the semantics of `config/paper-team/controller.md`.
A phase missed earlier today is caught up at the first later wake, in
data → research → engineering order. Statuses are `PENDING`, `RUNNING`,
`BLOCKED`, `COMPLETED`, `NO_ELIGIBLE_NEED` and `MISSED`; a prior-date phase that
never finished is marked `MISSED` rather than replayed.

The engineering phase is the only one with a side effect: the controller runs
the installed operator bridge (`team-evidence/operator/paper-team-bridge.py`
with its operator config, 180-second timeout) for one pending inbox request, and
dispatches the receipt to Strategy Manager. `applied` stays false; the bridge
never calls the applier. With nothing pending, the phase records
`NO_ELIGIBLE_NEED`.

## Idle, and how the doctor reports it

No participant may sit idle. Each one carries its own `max_idle_h`: 24h for the
native review roles, 6h for the steward, and its cadence for each deterministic
member (autopilot 0.1h, publisher 0.5h, market data 1h, doctor 1h, radar 2h,
controller 2h, CoinGlass 3h, liquidation clusters 3h, daily review 26h,
counterfactual 26h). Idle hours are measured from the last receipt, or from the
moment the controller first asked. A deterministic member that has never
produced a reading counts as idle; an unknown reading is not a healthy one.

The doctor's seventh check, `team`, reads `team-evidence/dispatch.json`:

| Condition | Status | Where it points |
| --- | --- | --- |
| No controller reading at all | `unknown` | has `com.danslab.trader-team-controller` ever run? |
| Last cycle older than 2h | `fail` | the controller did not fire; the whole team is stalled |
| Any installed role idle past its budget, or any dispatch blocked | `warn` | `dispatch.json`; the role's room in the Grok Bot app |
| Fresh cycle, nobody idle | `ok` | — |

Because the alert module is edge-triggered, a stalled team speaks once and
recovery speaks once.

## Linking each bot

Each native bot needs one paragraph in its own instructions so it knows where
its work arrives. The text below is generated from `trader/team/roster.py`
(`roster.linking`); paste it verbatim, after that role's existing instructions
and the standing contract.

**Grid Desk Lead** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Grid Desk Lead". Answer each one in Paper Grid Trading Team with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Data & Structure** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Data & Structure". Answer each one in Paper Grid Trading Team with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Risk Sentinel** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Risk Sentinel". Answer each one in Paper Grid Trading Team with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Performance Analyst** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Performance Analyst". Answer each one in Paper Grid Trading Team with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Research Scout** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Research Scout". Answer each one in Paper Grid Research & Data with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**X Setup Researcher** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "X Setup Researcher". Answer each one in Paper Grid Research & Data with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Strategy Manager** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Strategy Manager". Answer each one in Paper Grid Trading Team with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Technical Interpreter** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Technical Interpreter". Answer each one in Paper Grid Trading Team with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Discovery Auditor** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Discovery Auditor". Answer each one in Paper Desk Office with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Dan's Senior Developer** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Dan's Senior Developer". Answer each one in Paper Grid Research & Data with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

**Paper Desk Secretary** — paste into its native instructions:

> Team link. Every cycle, read https://danslabtrader.vercel.app/data/team.json and take only the dispatches whose "role_name" is "Paper Desk Secretary". Answer each one in Paper Desk Office with its dispatch_id alone on the first line, then at most five bullets. Do not answer another role's dispatch and do not start a new round; Dan's Senior Developer records the receipt for each answer you post.

## What Dan does in the app

1. Open each bot in the Grok Bot desktop app and paste that role's paragraph
   above at the end of its instructions. Save. The rooms and the two six-member
   rosters do not change.
2. Add one new bot named exactly **Discovery Auditor** in the Paper Desk Office
   room, with the role text from `config/paper-team/roles/discovery-auditor.md`
   prefixed by the standing contract (`config/paper-team/contract.md`), plus the
   linking paragraph above. Until that bot exists, the controller reports it as
   `NOT_INSTALLED` and its daily audit dispatch waits.
3. Flip `installed=False` to `True` for `discovery_auditor` in
   `trader/team/roster.py` once the bot answers, so the controller starts holding
   it to the same 24h idle budget as the other review roles.
4. Leave the native timer paused. There is one controller: the launchd job at
   minute 15. Copy the plist example to `~/Library/LaunchAgents`, then bootstrap
   it. It carries no credentials.
5. Ask existing Dan's Senior Developer to write one receipt per answered
   dispatch under `team-evidence/receipts/`. Without receipts, every dispatch
   blocks after two hours and every role is reported idle — which is the correct
   behaviour, but not a useful day.

_Last verified: 2026-09-13_
