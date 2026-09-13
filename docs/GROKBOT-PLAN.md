# GrokBot — goal and working plan

**Goal:** close every gap found in the 2026-09-13 ten-question audit, then audit the
whole system end to end including every GrokBot checkout and how the team connects.

Working tree for all of this: `~/ZCodeProject/GrokBot-prod` (branch `design/polish`).
That is what the installed services run. Build each item in a worktree under
`~/Sandbox/grokbot/wt-*`, gate with `npm run verify`, merge, push, verify live.

## Decisions taken by Dan (2026-09-13)

| Topic | Decision |
|---|---|
| Losing bot with working grids | **Hedge first**; close only if a better coin is free |
| Ranking | Keep `rank_score` deciding; **surface it in the UI** so shown order = deciding order |
| Liquidation clusters | **Derive from our own open interest**; pay only if the whole system works. No paying for tests. |
| Agent firewall | **Agents may influence entries directly** — build with kill switch + full audit trail |

## Task list

- [x] **T1 System doctor** — `python -m trader.doctor`, six outcome checks, one verdict.
      Merged `b8fc7dc`. Replays FAIL on both real 2026-09-13 causes.
- [ ] **T2 Schedule the doctor** — every 5 min, Telegram on FAIL only. LaunchAgent example
      in `config/launchd/`, installed copy is Dan's call.
- [ ] **T3 Counterfactual replay of skipped decisions** — for every `DECISION action=skip`,
      replay what that bot would have done. Turns ~211 skips/day into measurable outcomes.
      Unblocks the learner's sample starvation (Q7) and is the evidence base any agent
      influence must earn its say against.
- [ ] **T4 Liquidation-cluster derivation** — from `open_interest` + `ticker_snapshots`
      (394k rows each) + `maintain_margin`/`risk_limit`. ΔOI near a known price implies
      liquidation levels at standard leverage tiers. Feeds T5 and range construction.
- [ ] **T5 Hedge trigger** — when inventory loss outruns grid profit by a threshold, open an
      offsetting position instead of closing. Backtest against the 23 closed bots FIRST.
      Evidence: closed bots = grid +593.13 / directional −612.38.
- [ ] **T6 Opportunity-cost close** — non-risk closes (LABEL_FLIP, MAX_AGE, DROPPED) only fire
      when an eligible better candidate exists. Risk closes stay unconditional.
- [ ] **T7 Surface rank_score in the UI** — paper desk shows the number that decides order.
- [ ] **T8 Agent-influence interface** — agents may adjust entry decisions, every influence
      logged as a DECISION event with agent identity + delta, single flag reverts to
      deterministic-only. Build AFTER T3 so influence can be scored.
- [ ] **T9 Full system audit** — every GrokBot checkout, every service, every link and the
      team connections. Publish as an artifact, update the fleet dashboard.

## Hard constraints

- `~/ZCodeProject/ZmartyChat-paper-grid` is **sealed**: `experiment.py` re-hashes four source
  files every tick and freezes both paper accounts on any edit. Never edit it while running.
- Paper only. No exchange order, ever.
- Doctor and audit tooling stay **read-only**. No auto-restart of the autopilot.
- Every trading-logic change is backtested against the existing closed bots before merge.

## Progress log

- 2026-09-13 — T1 built, gated (342 paper + 445 trader), merged `b8fc7dc`, live: `circle: WARN`
  (CoinGlass 8/9, LONGXIA unservable — correct).
