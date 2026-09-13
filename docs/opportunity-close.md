# Opportunity-cost close (T6) and rank_score in the UI (T7)

Owner decisions, 2026-09-13 (`docs/GROKBOT-PLAN.md`):

- *Losing bot with working grids*: **hedge first; close only if a better coin is free.**
- *Ranking*: **keep `rank_score` deciding; surface it in the UI so shown order = deciding order.**

## T6 — what changed

`LABEL_FLIP`, `DROPPED` and `MAX_AGE` no longer close a bot on their own. After the
learned min-hold gate, `policy.decide()` asks
`trader/autopilot/opportunity.better_candidate_exists()` one question: with this
bot's slot already free, is there a qualified radar candidate whose watchlist
`score` beats the score the bot was admitted on by at least
`constants.PROMOTION_MARGIN` (10 points)? If not, the bot is kept and keeps
trading its grids; the normal path resumes the moment the label flips back or a
better coin appears.

Unconditional, unchanged: `RANGE_BREAK`, `STOP_LOSS`, `RISK_LIMIT` (account
protection) and `PROFILE_UPDATE` (which rebuilds a stale specification rather
than abandoning a coin).

### Ceiling on the deferral

A deferral must not become an unbounded stale position: "no better candidate
right now" can persist for days in a thin market, and `STOP_LOSS` at −12% of
notional is a floor, not a policy. So
`constants.OPPORTUNITY_HOLD_MAX_AGE_HOURS = 2 * MAX_AGE_HOURS` (144h) caps it —
once a bot's age passes the ceiling the gate stops deferring and the `MAX_AGE`
close proceeds unconditionally, with the usual `DECISION action=close` event.

The ceiling is **`MAX_AGE`-only**. `LABEL_FLIP` and `DROPPED` are the radar
changing its opinion rather than the bot going stale, so they keep deferring at
any age: a bot whose label flipped back and forth for a week is not stale, it is
simply unlucky in its radar coverage.

One related consequence worth knowing: while the radar is unavailable, `labels`
and `sections` are empty, so the gate sees no candidate and defers — except past
the ceiling, where age alone ends it.

Candidate admission reuses policy's own helpers — `_candidates`, `_eligibility`,
`_trend_gate_blocks`, `_cooldown_gate_blocks` — so there is exactly one
definition of "eligible candidate". Two details matter:

- **The slot is judged as already free.** Admission runs against the portfolio
  with this bot removed. Judged with the bot still open, `bot_capacity` would
  block every candidate at 5/5 and the gate would hold forever.
- **The bot is never its own replacement.** A non-risk close puts the symbol in
  a 6h cooldown, so open symbols (including this one) are excluded.

Direction search mirrors `decide()`: the slot's own direction first, then
`NEUTRAL`, `LONG`, `SHORT`.

### Observability

- Rule code **19 `opportunity_hold`** in `policy.DECISION_RULES`.
- One `RULE_BLOCK` per bot per radar scan (`rule=19`, numeric fields `bot_score`,
  `best_candidate_score`), latched on `wrapper['rule_blocks']` and reset when
  `scan_id` changes.
- A `DECISION action=skip` carrying `rule_blocks=[19]` on every deferred pass, so
  the deferral is visible in the events feed even while the latch is closed.

### Kill switch

`constants.OPPORTUNITY_COST_CLOSE = True`, overridable by the learned rule
`opportunity_cost_close` (boolean). The rule is **absent** from `EMPTY_RULES`, like
`radar_flip_hysteresis_cycles`, so every existing store stays valid;
`rules.validate_store` accepts `true`/`false`/absent and rejects anything else.
With the switch off the close path is the pre-T6 one, byte for byte
(`trader/tests/test_opportunity_close.py::KillSwitchTests`).

### Bots opened before T6

Live wrappers closed before the decision-context change carry no
`decision_context`, so their opening score reads as `0.0` and any qualified
candidate clears the margin. Unknown history therefore falls back to the old
behaviour instead of holding a bot we cannot evaluate.

## T6 — backtest evidence

`python3 -m trader.review.backtest_opportunity` (read-only: `state.json`,
`events*.jsonl`, and the market database opened with `mode=ro&immutable=1`).
For each non-risk close it asks whether a replacement actually existed — an
`OPEN` in the same or the next decision pass — and replays the bot's own opening
parameters over 1m klines past the close to a mark six hours later.

Run 2026-09-13 against the live ledger (23 closed bots; 11 `RANGE_BREAK`,
9 `PROFILE_UPDATE`, 3 `LABEL_FLIP`):

```
bot  symbol       reason      slot     held_h  grids  net@close  replay@close  replay@+6h  delta  replacement
-------------------------------------------------------------------------------------------------------------
  2 RAYUSDTM     LABEL_FLIP  NEUTRAL    0.1    0/0      -0.05         -0.02        -0.50   -0.5 none
 10 RAVEUSDTM    LABEL_FLIP  NEUTRAL    1.2  18/20      -4.42         -0.82       -63.27  -62.4 DOTUSDTM [ended RANGE_BREAK]
 22 ALLOUSDTM    LABEL_FLIP  LONG       7.2  15/19      -4.33         -2.24       -13.07  -10.8 CAKEUSDTM

Non-risk closes examined: 3 (3 LABEL_FLIP, 0 other).
Would have been HELD by the gate (no replacement OPEN in the same or next decision pass): 1 of 3 LABEL_FLIP closes.
  bot 2 RAYUSDTM: net -0.02 at close -> -0.50 at +6h (-0.47).
Sum of would-have-been change at +6h: -0.47 USDT across 1 bot(s).
Sample size is three. This is an anecdote, not evidence that the gate is profitable.
```

`grids` is *actual at close / replayed at close* — the replay's fidelity check.

### Verdict, honestly

**One** of the three `LABEL_FLIP` closes (bot 2, RAYUSDTM) had no replacement in
the same or next pass and is the one the gate would have held. Held six hours
longer it would have gone from −0.02 to −0.50 USDT: **−0.47 worse**.

The other two had a replacement open immediately (DOTUSDTM, CAKEUSDTM), so the
gate would have let them close — which is fortunate, because holding them was
worse still: RAVEUSDTM would have run into a `RANGE_BREAK` at −63.27 (−62.4) and
ALLOUSDTM would have marked −13.07 (−10.8).

So on this sample the gate neither helped nor hurt materially (−0.47 USDT on one
bot), and the "hold the loser" instinct would have been **wrong** on the two bots
the gate correctly lets go. Three samples is an anecdote. The case for T6 rests
on the owner decision and on the structural argument (a non-risk close realises a
loss to free a slot nobody wants), not on this table.

### Limitations of the replay

- 1m OHLC walks the whole candle range each minute, while the live daemon sees
  discrete ~10s prices. Grid completions are therefore slightly over-counted
  (18→20, 15→19) and replayed nets are mildly optimistic. Even so, holding lost
  in all three cases.
- The replay refreshes `risk_metadata_at_ms` with the clock (no metadata feed
  exists offline), so `RISK_LIMIT` staleness closes are not exercised; range
  breaks, fees, funding and reserve top-ups are the real engine.
- "Would have been held" is inferred from replacement `OPEN` events, not from the
  gate itself: the live closed bots predate `decision_context`, so their opening
  radar score is not on record and the score margin cannot be recomputed.
- Kline coverage for the window was 356/365, 430/429 and 790/794 minutes.

## T7 — Ranking

**Which number decides entry order? `rank_score`, not the 100-point score.**
Verified in source:

- `trader/autopilot/watchlist.py::_order` sorts Core and Bench by
  `entry['score']` — the 100-point watchlist number. That decides *seats*
  (Core vs Bench) and the order the desk renders.
- `trader/autopilot/policy.py::_candidates` iterates each radar section sorted by
  `-row['rank_score']` (`-row['atr_1h_pct']` for the Movers section) and
  `decide()` opens the first admissible row. That decides *entries*.

The two orders are not the same. Live core, 2026-09-13:

| shown (score) | score | rank_score | radar section |
|---|---|---|---|
| FFUSDTM | 61.9 | 4.34 | NEUTRAL |
| NIULAIUSDTM | 58.9 | 9.41 | NEUTRAL |
| PUMPUSDTM | 57.7 | 6.65 | TURNING-DOWN |
| MYXUSDTM | 55.7 | 2.13 | SHORT |
| CAKEUSDTM | 52.1 | 1.62 | NEUTRAL |

Inside the NEUTRAL section the deciding order is NIULAI (9.41) → FF (4.34) →
CAKE (1.62), while the desk shows FF first. Both numbers are real; keeping only
one would hide a decision.

### What the desk shows now

- Each Core/Bench card carries a second chip, `rank NN`, beside `score / 100`,
  with a `title` explaining: *entry order inside a radar section is decided by
  rank_score (oscillation x liquidity); score is the watchlist gate*.
- When the displayed order differs from the rank order, a one-line note appears
  in the watchlist section header: *Cards are ordered by watchlist score; entries
  pick by rank.* It stays hidden when the two agree.
- `watchlist._entry` carries `rank_score` and `expected_grids_per_hour` into the
  snapshot; `paper_grid/public_autopilot.watch_entry` publishes them as optional
  non-negative numbers (absent on entries from an older scan, rejected if
  negative, non-finite, boolean or free text).

No CSP change, no inline handlers, no new network origin; the page's existing
`el`/escaping helpers do the rendering.

_Last verified: 2026-09-13_
