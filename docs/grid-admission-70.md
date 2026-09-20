# Chart-supported capacity for new grid entries

New paper admissions require 70–200 configured grids. The selector searches
confirmed support/resistance pairs in ascending width, rejecting pairs that
cannot fit 70 grids with the existing minimum margin return strictly above 1%
after both modeled fill fees at 5x. It then continues to the next confirmed
pair. This is not permission to invent levels, move a stop arbitrarily, or
relax the floor when no candidates qualify. Grid count is not open-order count:
a Neutral bot can have two books and twice the number of open orders.

Existing 60/40 trend and 50/50 Neutral split checks, fee rates, liquidity,
quantity sizing and liquidation checks remain unchanged. Consequently Dan's
RAY examples still fail the hard split tolerance: resolving whether these
ratios are preferences is a separate policy decision. No new retest thresholds
are invented in this change. Funding remains outside the displayed margin-return
floor; it must not be described as a verified all-in return.

The common `layout_valid` gate rejects stale low-grid radar rows during new
admission. Existing positions do not call this new-admission gate while being
advanced; their saved lines, range, quantity and close rules remain in force.
No live state migration, bankroll reset, service restart or deployment is part
of this PR. An empty qualified radar means wait, not silently admit fewer grids.

Validation includes previously accepted 65/32-grid ranges, a confirmed outer
pair that supports at least 70 grids, original/rounded chart provenance, old
radar rejection and continued processing of an existing 20-grid bot.

## Single current-window check

Read-only CLI run at 2026-09-20T09:43:40.947000+00:00 against the existing phase-2-20260911 market database, with `--no-liquidation-clusters`, no Telegram and no JEV calls. Output was written to `/tmp/grokbot-grid70-radar.json`, never the live radar.

369 rows, 51 passing liquidity, zero trade-section candidates (three majors direction-only). Among the liquid rows: 34 INSUFFICIENT_GRID_ROOM, 11 MISSING_STRUCTURE, 6 ENTRY_SPLIT. This is an observation at one instant, not a backtest or proof that no valid setup will occur. The candidate is not deployed: activation would currently stop new admissions. Ratio interpretation and retest semantics remain unresolved; do not loosen them silently.

Both local gates pass: 1,191 tests (39 Node, 356 paper, 772 trader, 24 team), plus staged-secret scan.
