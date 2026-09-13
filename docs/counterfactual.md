# Counterfactual replay of skipped decisions

The autopilot skips far more candidates than it opens — 860 `DECISION action=skip`
events over two days against roughly six opens a day. The 07:00 learner therefore
studies a handful of bots and nothing else, while the interesting question goes
unasked: **what did the desk's refusals cost, and what did they save?**

This feature answers it in two halves.

1. `trader/autopilot/counterfactual.py` — recorded live, one line per skipped
   candidate that could actually have been opened.
2. `trader/review/counterfactual.py` — replayed after the fact against stored 1m
   candles, and summarised into the daily review.

## What is recorded

After every decision pass the runtime hands the pass's events to the recorder,
which keeps a skip only when **every** rule that blocked it is a capacity or
policy choice:

| code | rule | why it is replayable |
|---|---|---|
| 3 | `bot_capacity` | the setup was fine, the desk was full |
| 5 | `duplicate_symbol` | already trading that symbol |
| 6 | `cooldown` | recently closed |
| 7 | `direction_cap` | too many bots on that side |
| 8 | `major_cap` | too many majors |
| 9 | `movers_cap` | too many trend movers |
| 16 | `learned_trend_alignment` | a learned rule said no |
| 17 | `learned_symbol_cooldown` | a learned rule said no |
| 18 | `learned_min_hold` | a learned rule said no |

Any other block — `range_not_verified`, `invalid_profile`, `invalid_layout`,
`missing_liquidity`, `missing_live_price`, `low_grid_rate`, `insufficient_equity`,
`missing_radar`, `no_candidates` — disqualifies the candidate. Those describe a
setup that *could not* have been opened, and replaying one would invent a trade
the desk never had. A skip carrying a mix (say `bot_capacity` **and**
`invalid_layout`) is dropped for the same reason.

For each survivor the recorder rebuilds the exact specification the desk would
have used, via `policy.profile(row, direction, bot_id=0)` on the same radar row
the pass saw, marked with the same live price. If `profile` raises, the candidate
is dropped. Each record carries the decision identity (`ts_ms`, `scan_id`,
`symbol`, `direction`, `rule_blocks`), the headline numbers (`price`,
`range_low`, `range_high`, `grids`, `grid_interval`, `leverage`,
`notional_usdt`, `funding_pct`, `expected_grids_per_hour`, `rank_score`,
`score`) and the full `spec` dict, so the replay is the same bot, not an
approximation of one.

Records are deduped per `(scan_id, symbol, direction)` and appended to

    <autopilot runtime dir>/counterfactual/candidates-YYYY-MM-DD.jsonl

one `O_APPEND` write per pass, mode `0600`, **UTC** days (the same rotation the
event log uses), 30 calendar days of retention. Note the asymmetry with the daily
review, which works on *local* days.

The runtime hook is research-only and fail-safe by contract: any exception is
printed to stderr as a type name and swallowed. It cannot change trading state,
cannot fail a pass, and cannot delay a decision.

## Replaying a day

    python3 -m trader.review.counterfactual \
        --database ~/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3 \
        --candidates-dir ~/Sandbox/grokbot/autopilot/counterfactual \
        --date 2026-09-13 \
        --out ~/Sandbox/grokbot/reports/counterfactual-2026-09-13.json \
        [--horizon-hours 24]

Each candidate is opened on the real paper engine at its recorded price and
timestamp, stepped over the stored 1m candles, and closed on the live rules: the
first `RANGE_BREAK` or `STOP_LOSS` the engine emits, otherwise at the horizon
(`exit_reason: HORIZON`). The output is written atomically:

```json
{"schema_version": 1, "date": "...", "generated_at_ms": 0, "horizon_hours": 24,
 "candidates": 0, "replayed": 0, "skipped": 0, "outcomes": [], "summary": {}}
```

Each outcome carries `symbol`, `direction`, `rule_blocks`, `hold_h`, `grids`,
`grid_profit`, `fees`, `funding`, `net`, `grids_per_hour`, `exit_reason` and
`partial`. The summary aggregates `count`, `sum_net`, `median_grids_per_hour`
and `share_with_positive_net` per rule-block name, per direction, and as one
headline total. A candidate blocked by two rules counts under both names, so the
per-block sums overlap by design; the total does not.

Run it before the daily review. `trader.review.daily` picks up
`counterfactual-<date>.json` next to its `--out` markdown automatically (override
with `--counterfactual PATH`), renders a short **Skipped decisions, replayed**
block inside section 3, and — only above a $25 threshold — raises one of two
**tier-2 advisory** notes, which never auto-apply:

- `capacity_cost` — `bot_capacity` + `direction_cap` skips replayed to more than
  $25 of net over the day. The slot caps turned away money.
- `learned_rule_cost` — the `learned_*` gates did. A learned rule is blocking
  profitable entries and should be re-examined.

A negative sum says the block *saved* money and raises nothing.

## Limits — read before believing a number

- **1m-snapshot fills.** The replay walks `open → extreme → extreme → close` per
  minute. Real fills happen inside that minute in an order no snapshot records,
  so grid counts are indicative, not exact.
- **The horizon is arbitrary.** 24h is a convention, not a thesis. A bot that
  looks green at 24h can be red at 30h; `exit_reason: HORIZON` means "still
  open when we stopped looking", nothing more.
- **Partial coverage is flagged, not fixed.** When the stored candles do not
  cover the whole horizon the candidate is still replayed with what exists and
  `partial: true` is set. Treat those outcomes as lower-confidence.
- **Funding is approximated.** The live desk books funding from observed
  settlement rates; a replay has no settlement ledger, so it charges the
  snapshot `funding_pct` at the engine's 8h boundaries. Better than pretending
  funding is free, but not the real number.
- **No portfolio interaction.** Each candidate is replayed alone. In reality it
  would have consumed equity, a slot and a direction cap, and would have changed
  which *other* bots opened. Summing every skipped candidate's net is an upper
  bound on a world that could not have existed.
- **`learned_min_hold` is a close gate, not an entry gate.** Those skips block a
  *close*, and the replay treats them like any other candidate — read that block
  as "the symbol was still worth holding", not as a missed entry.
- **This is not a claim of edge.** It measures the cost of a refusal under one
  set of assumptions. Nothing here auto-applies, and nothing here should be used
  to justify raising a cap without a separate forward test.

_Last verified: 2026-09-13_

## Which rule gets charged for a refused entry

An entry the desk turned away for two reasons is not money any one of those
rules cost us: lift only one and it is still refused. So the summary carries
two views, and they answer different questions.

| View | Question it answers |
|---|---|
| `by_rule_block` | Which rules were involved at all? An entry counts under each of its blocks, so these sums overlap. Descriptive only. |
| `by_sole_block` | What would lifting exactly this rule have earned? Only entries refused for that one reason. This is what the advisories read. |
| `total` | Every replayed entry, counted once. |

The first real record, on 2026-09-13, is why this distinction exists. It was a
second MYXUSDTM short on a coin the desk already held, refused by both the slot
cap and the duplicate-symbol rule. Charged to the slot cap it would have argued
for raising a cap that still would not have opened it.

A consequence worth stating: an entry refused only by `duplicate_symbol` is
replayed and reported, but it models a book where two bots run the same coin.
That is a real policy question, and it is not the same question as the slot cap.

## When it runs

The recorder runs inside the autopilot's decision pass, so it needs no schedule.
The replay is a phase of the existing 07:00 job, `~/.openclaw/scripts/daily-review-run.sh`,
added 2026-09-13. It writes `reports/counterfactual-<date>.json`, which
`trader.review.daily` then discovers by default path with no flag.

That phase is fail-open on purpose. The replay is research input; the review that
follows it applies learned rules. A replay failure logs a warning and the review
runs exactly as it did before this existed.

