# Prospective paper grid pair evidence

Completed GRID events now carry `book`, `closing_fill_id`, `closing_fee`,
`pair_evidence` and `funding_known`. A known opening also provides
`opening_fill_id`, `opening_ts_ms`, `opening_price`, `opening_fee`, `seeded`,
`holding_ms` and `net_before_funding`. Ordinary FILL events carry the same
book-local `fill_id`; identify them by bot_id + book + fill_id. A GRID is
uniquely referenced by bot_id + book + closing_fill_id. Neutral books have
separate counters and must never be conflated.

Opening evidence is stored on the pending closing order and survives JSON
state persistence. Seeded closing orders share their aggregate seed fill id,
are marked seeded=1, and receive the actual booked aggregate seed fee divided
by seeded order count (equal quantities). The seed is established during OPEN,
not emitted as an ordinary grid FILL event. Seeding never completes a grid.

The net figure subtracts the booked opening and closing fill fees from the
existing grid profit. It is NOT net after funding and does not alter realized
PnL, grid profit, fees, equity, matching or close rules. `funding_known=0` is
explicit: there is no synthetic zero funding allocation. The timestamps are
paper-engine observation timestamps; candle recovery is not exchange tick
precision. Do not use holding_ms as an exchange execution-latency measurement.

Old orders without pair evidence emit pair_evidence=0 and omit opening/hold/net
fields. We do not infer a historical opening fee from today's schedule or
rewrite old events. A new roundtrip after rollout can acquire complete opening
provenance even on an older bot. The non-v2 single-book seeded case retains
unknown provenance because its historical profit basis is not actual entry.

Tests cover Long, Short and Neutral roundtrips, seed fee allocation, JSON restart,
duplicate-tick idempotence, event validation and legacy unknowns. Both repository
gates must pass before commit. No runtime activation, state migration or dashboard
change is included. Funding allocation remains a separate reconciliation task.

Refs #64. Local validation: 1,190 tests (39 Node, 356 paper, 771 trader, 24 team).
