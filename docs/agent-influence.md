# Agent-influence interface (T8)

Owner decision, 2026-09-13 (`docs/GROKBOT-PLAN.md`): **agents may influence
entries directly** — built with a kill switch and a full audit trail.

This document is the contract. Read it before writing the file, before turning
the channel on, and before believing any number it produces.

## The one channel

The ten native assistants in the Grok Bot desktop app cannot write to this
engine. Their operations steward (the *Dans Senior Developer* role, which owns
sanitized `team-evidence/`) writes **one JSON file**; the desk reads it at the
top of a decision pass.

```
~/Sandbox/grokbot/team-evidence/influence.json   (trader.autopilot.influence.DEFAULT_PATH)
```

**This file is the only way any agent reaches the engine.** There is no network
call, no socket, no model in the trading loop, and no other hook. Nothing in
`trader/` imports a model client for this purpose and nothing ever should.

## Hard bounds

1. **Influence applies only to candidates the deterministic policy already
   admitted.** The file is applied to the section mapping `policy.decide()`
   builds from the qualified, Core-seated radar rows, and every gate still runs
   afterwards on whatever survives: `range_not_verified`, `insufficient_equity`,
   `missing_liquidity`, `invalid_profile`, `invalid_layout`,
   `missing_live_price`, sizing, the liquidation buffer, and the
   direction/major/movers caps. **None of them can be softened by any
   influence.** An agent cannot make an ineligible coin eligible: a record that
   names a coin the policy did not admit is a silent no-op.
2. **Two verbs, nothing else.**
   - `BOOST` adds a bounded delta to the candidate's *ordering score inside its
     own radar section*. It is added in `policy._candidates`' sort key only —
     `rank_score` (or `atr_1h_pct` in Movers) is never rewritten, so no
     eligibility, profile, sizing or spacing computation ever sees it. A boost
     cannot move a row into another section: `turning_up` is still read before
     `long`, whatever the delta.
   - `VETO` removes the candidate from consideration for this scan.
3. **The delta is capped.** `constants.INFLUENCE_MAX_DELTA = 10.0` score points
   per candidate (across all agents, so two agents cannot stack past it), and
   `constants.INFLUENCE_MAX_TOTAL_DELTA = 20.0` granted across the whole scan.
   Over-cap values **clamp**; they never reject the file. Every clamp is logged
   (`influence_clamped=1` on the event, `clamped` in the audit entry).
4. **Influence expires.** Records carry no timestamp of their own: the
   envelope's `generated_at_ms` is their age, so one forgotten file expires all
   at once. Older than `constants.INFLUENCE_MAX_AGE_MIN = 90` minutes and the
   file is ignored entirely.
5. **Fail-closed everywhere.** Any problem ignores the **whole file**; the desk
   behaves exactly as it does today, logs one `ERROR` event
   (`code = constants.INFLUENCE_ERROR = 6`, `problems = <count>`), and never
   raises into the trading loop. A scan that admitted no candidate at all (a
   missing radar, say) has nothing to influence and does not read the file
   either, so a radar outage cannot spray `ERROR` events.

An agent can also never *force* a close. The influenced sections feed the
opportunity-cost gate (T6), where a veto can only shrink the replacement pool —
that makes the gate hold a bot longer, never close one. Boosts are invisible
there: that gate compares the 100-point watchlist `score`, which influence does
not touch.

## Schema

```json
{
  "schema_version": 1,
  "generated_at_ms": 1789200000000,
  "records": [
    {
      "agent_id": "x_setup_researcher",
      "symbol": "PUMPUSDTM",
      "direction": "SHORT",
      "verb": "BOOST",
      "delta": 6.0,
      "reason_code": 1,
      "evidence_ref": "2026-09-13.x-setup-0412#PUMPUSDTM"
    }
  ]
}
```

Worked example: [`config/paper-team/influence.example.json`](../config/paper-team/influence.example.json)
(validated by `trader/tests/test_agent_influence.py::ExampleFileTests`).

| field | rule |
|---|---|
| `schema_version` | exactly `1` |
| `generated_at_ms` | finite number, not in the future, not older than 90 minutes |
| `records` | list, at most 40 entries; file at most 64 KiB |
| `agent_id` | one of the closed roster below |
| `symbol` | `[A-Z0-9]{1,32}` **and** present in this scan's radar universe |
| `direction` | `LONG`, `SHORT` or `NEUTRAL` — the slot direction, not the radar label |
| `verb` | `BOOST` or `VETO` |
| `delta` | finite, `>= 0`; **must be `0` for a `VETO`** (a veto does not reorder) |
| `reason_code` | one of the closed vocabulary below |
| `evidence_ref` | 1–120 chars, `A-Za-z0-9-_.:#@+=,()` and single spaces; no path separators, no tabs or newlines, no leading/trailing space, no double spaces |

Every record must carry **exactly** those seven keys — no more, no fewer — and
the envelope exactly those three. An unknown key is a schema error.

### Closed agent roster

`trader.autopilot.influence.ROSTER` — the eight review roles, plus the steward
who is the file's only writer:

| id | desk role |
|---|---|
| `grid_desk_lead` | Grid Desk Lead |
| `strategy_manager` | Strategy Manager |
| `data_and_structure` | Data & Structure |
| `technical_interpreter` | Technical Interpreter |
| `risk_sentinel` | Risk Sentinel |
| `performance_analyst` | Performance Analyst |
| `x_setup_researcher` | X Setup Researcher |
| `research_scout` | Research Scout |
| `dans_senior_developer` | Dans Senior Developer — operations steward, sole writer |

The user-facing *Paper Desk Secretary* is deliberately absent: it holds no
analytical opinion and makes no board writes.

### Closed reason vocabulary

`trader.autopilot.influence.REASON_CODES` — free text never reaches the engine.

| code | meaning |
|---|---|
| 1 | `x_setup_evidence` — an original X/social setup read |
| 2 | `structure_doubt` — the range or level looks unconvincing |
| 3 | `risk_objection` — exposure, correlation or funding objection |
| 4 | `performance_history` — this coin/profile has a measured record |
| 5 | `liquidation_cluster_proximity` — a cluster sits against the entry |
| 6 | `research_corroboration` — papers/docs/video research agrees |

### Every fail-closed case

Each of these ignores the **entire file** (one `ERROR` event, no influence
applied, deterministic desk):

| case | reported problem |
|---|---|
| file missing, unreadable, not UTF-8, or not JSON | `unreadable` |
| file larger than 64 KiB | `too_large` |
| envelope is not an object, or has unknown/missing keys | `not_an_object`, `file_keys` |
| `schema_version` is not `1` | `schema_version` |
| `generated_at_ms` missing or non-finite | `generated_at_ms` |
| `generated_at_ms` in the future (clock skew) | `future` |
| older than `INFLUENCE_MAX_AGE_MIN` | `stale` |
| `records` not a list, or more than 40 | `records`, `too_many_records` |
| record not an object, or wrong key set | `record_N_not_an_object`, `record_N_keys` |
| `agent_id` outside the roster | `record_N_unknown_agent` |
| malformed symbol | `record_N_invalid_symbol` |
| symbol outside this scan's radar universe | `record_N_unknown_symbol` |
| bad direction / verb | `record_N_invalid_direction`, `record_N_invalid_verb` |
| non-finite, negative, or non-zero-on-a-veto delta | `record_N_invalid_delta` |
| `reason_code` outside the vocabulary (booleans included) | `record_N_invalid_reason_code` |
| `evidence_ref` too long, empty, or carrying a separator/whitespace | `record_N_invalid_evidence_ref` |
| the same `(agent_id, symbol)` twice | `record_N_duplicate` |

Note the sharp edge in `record_N_unknown_symbol`: writing about a coin that has
since left the radar discards the **whole** file, not just that record. That is
deliberate — a file the desk cannot fully understand is a file it does not act
on.

## How the steward writes it

One write per cycle, atomic, private:

```python
import json, os, tempfile, time
from pathlib import Path

target = Path.home() / 'Sandbox' / 'grokbot' / 'team-evidence' / 'influence.json'
payload = {'schema_version': 1, 'generated_at_ms': int(time.time() * 1000),
           'records': records}
handle, temporary = tempfile.mkstemp(dir=target.parent, prefix='.influence-')
with os.fdopen(handle, 'w', encoding='utf-8') as stream:
    json.dump(payload, stream, allow_nan=False)
os.chmod(temporary, 0o600)
os.replace(temporary, target)          # atomic: a reader never sees half a file
```

Rules for the steward:

- **One writer.** Only `dans_senior_developer` writes this path.
- **One write per cycle**, `0600`, tmp+rename. Never append, never edit in
  place, never leave a partial file.
- **Rewrite the whole file every cycle**, including `generated_at_ms`. Stopping
  writing is how influence stops: the file goes stale within 90 minutes and the
  desk returns to pure determinism on its own.
- **Sanitized only.** No credentials, no free text, no paths. `evidence_ref` is
  an opaque handle into `team-evidence/`, not a filename.

## Audit trail

Every applied influence emits a `DECISION` event with `action='influence'`,
carrying numeric fields only plus the agent id:

```
ts_ms, bot_id=0, symbol, type=DECISION, action=influence,
direction, radar_direction, radar_score, expected_grids_per_hour,
range_width_pct, funding_rate, kucoin_ok,
rule_blocks=[20|21], agent_id, reason_code, influence_delta, influence_clamped
```

- `policy.DECISION_RULES['influence_boost'] = 20`
- `policy.DECISION_RULES['influence_veto'] = 21`

`agent_id` is the only non-numeric field in the schema
(`storage.validate_event` accepts it for `action='influence'` and nothing else);
the public snapshot publishes the numbers and drops the id.

### How a veto is scored by the replay

A veto that turned away an entry the desk could otherwise have filled also emits
a normal `DECISION action=skip` with `rule_blocks=[21]` — only when the pass
actually had a vacancy, and only after the same `_eligibility` (and
`require_live_prices`) check `fill()` would have run, so a veto of an entry that
was going to be refused anyway is not counted. Rule 21 is in
`counterfactual.POLICY_BLOCK_CODES`, so the nightly recorder writes that
candidate's exact bot specification to `candidates-<date>.jsonl` and
`trader.review.counterfactual` replays it over 24h on the paper engine. That is
the whole point of building T8 after T3: the replay already priced the desk's
own refusals, and now it prices the agents' refusals on identical terms.

`trader.autopilot.influence_audit.summarize(events, outcomes)` joins the two on
`(ts_ms, symbol, direction)` — every event in one decision pass shares that
pass's `now_ms`, and the recorded candidate carries the same `ts_ms`, so the
timestamp identifies the scan. Accounting, stated honestly:

- A matched **veto** priced at replayed net `N` contributes `-N`. Vetoing a
  profitable entry is a cost; vetoing a losing one is a saving.
- A **boost** has no counterfactual by construction. If it worked, a real bot
  opened and its P&L is in the ordinary ledger. Boosts are therefore **counted,
  never priced**, and a *matched* boost means the boost failed — the candidate
  was skipped for some other reason anyway.
- Displacement is not modelled: a boost that pushes another candidate out of a
  slot is not charged for that candidate's replayed net.

The daily review gains one additive section, *"Agent influence, priced"*, with a
per-agent ledger, and one tier-2 advisory, `influence_cost`, when an agent's net
influence is worse than `-$25` over the day (`rules.INFLUENCE_COST_USD`). Like
every tier-2 note it is **advisory only and never auto-applies** — the only
levers are the kill switch and the steward's file.

## Turning it on

Ships dark. Either lever works; the learned rule wins when present.

1. Constant: `trader/autopilot/constants.py` → `AGENT_INFLUENCE_ENABLED = True`,
   then restart the autopilot.
2. Learned rule: add `"agent_influence_enabled": true` to the `rules` object in
   `~/Sandbox/grokbot/autopilot/learned-rules.json`. It is **absent** from
   `EMPTY_RULES`, so every existing store stays valid; `rules.validate_store`
   accepts `true`/`false`/absent and rejects anything else. No restart needed —
   the store is mtime-cached.

## Turning it off, in one move

Set `agent_influence_enabled` to `false` in the learned-rules store (or
`AGENT_INFLUENCE_ENABLED = False` and restart). With the switch off the file is
**not even read** — `influence.load` is never called — and decisions and events
are byte-identical to the deterministic desk, proven by
`trader/tests/test_agent_influence.py::KillSwitchTests`.

Deleting the file is the steward's kill switch rather than the owner's: it takes
effect on the next scan and leaves the channel armed.

## Limits — read before believing a number

- The replay prices a veto with 1m OHLC fills over an arbitrary 24h horizon,
  against a bot that never competed for real capital. It measures the cost of a
  refusal; it is not a claim of edge. See `docs/counterfactual.md`.
- Boost effectiveness is not measured at all (see above). "Agent X is net
  positive" can only ever mean "agent X's *vetoes* were net positive".
- A delta of 10 points is enormous next to live `rank_score` values (roughly
  1–10). Inside a section, one maximal boost effectively decides the order. The
  bound that matters in practice is not the size of the delta but the fact that
  it cannot cross a section or a gate.
- Influence is evaluated once per decision pass, over the Core watchlist only.
  A record naming a Bench coin does nothing.

_Last verified: 2026-09-13_
