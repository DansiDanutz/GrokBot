# GrokBot — goal and working plan

**Goal:** close every gap found in the 2026-09-13 ten-question audit, link and
orchestrate every bot in the team, and audit the whole system end to end
including every GrokBot checkout and how the team connects.

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
- [x] **T2 Edge-triggered alerting** — merged `9aceed0`. Notifies only when the set of
      failing checks changes. LaunchAgent example shipped; **not installed** — that is
      Dan's deliberate act because it enables an unattended job that sends Telegram.
- [ ] **T3 Counterfactual replay of skipped decisions** — for every `DECISION action=skip`
      with policy/capacity blocks only, replay what that bot would have done over 24h.
      Turns ~430 skips/day into measurable outcomes. Feeds the starved learner (Q7) and
      is the evidence base any agent influence (T8) must earn its say against.
- [x] **T4 Liquidation-cluster derivation** — merged `1e754e6`, gate green (473 trader).
      First live file: 60 symbols, e.g. 4USDTM carries $14,394 of implied short
      liquidations 1.25% above spot. Producer plist shipped as an example, not installed.
- [ ] **T5 Hedge trigger** — when inventory loss outruns grid profit and price approaches a
      cluster, open an offsetting leg instead of closing. Backtest against the closed bots
      FIRST. Evidence today: closed bots = grid +593.13 / directional −760.62.
- [ ] **T6 Opportunity-cost close** — non-risk closes (LABEL_FLIP, MAX_AGE, DROPPED) only
      fire when an eligible better candidate exists. Risk closes stay unconditional.
- [ ] **T7 Surface rank_score in the UI** — the desk shows the number that decides order.
- [ ] **T8 Agent-influence interface** — agents may adjust entry decisions, every influence
      logged as a DECISION event with agent identity + delta, single flag reverts to
      deterministic-only. Build AFTER T3 so influence can be scored.
- [ ] **T9 Full system audit** — every GrokBot checkout, every service, every link and the
      team connections. Publish as an artifact, update the fleet dashboard.
- [ ] **T10 Team orchestration controller** — `python -m trader.team`, hourly :15, replaces
      the dead Codex heartbeat. Derives events from the live circle, dispatches each to the
      role that owns it, reconciles receipts, escalates idle and blocked roles, publishes
      `/data/team.json` so every native bot can read its own work. Adds a seventh doctor
      check so a stalled team is visible. Adds the Discovery Auditor role (Q5).

## Hard constraints

- `~/ZCodeProject/ZmartyChat-paper-grid` is **sealed**: `experiment.py` re-hashes four source
  files every tick and freezes both paper accounts on any edit. Never edit it while running.
- Paper only. No exchange order, ever.
- Doctor and audit tooling stay **read-only**. No auto-restart of the autopilot.
- Every trading-logic change is backtested against the existing closed bots before merge.
- Enabling a new unattended job (launchd) or sending Telegram is Dan's authorization, not ours.

## Progress log

- 2026-09-13 — T1 built, gated (342 paper + 445 trader), merged `b8fc7dc`, live: `circle: WARN`
  (CoinGlass 8/9, LONGXIA unservable — correct).
- 2026-09-13 — T2 merged `9aceed0`; plan committed `d0f4dfb`.
- 2026-09-13 — Plan re-audited against the ten questions. T3/T4/T6+T7/T10 started in parallel
  worktrees `wt-cf`, `wt-liq`, `wt-oc`, `wt-team`. Baseline gate re-verified green on
  `design/polish`: 342 paper + 451 trader + 9 node.
- 2026-09-13 — T4 merged `1e754e6`; gate re-run on the merged result: 342 paper + 473 trader + 38 node, green.
- 2026-09-13 — Stale `~/ZCodeProject/GrokBot` checkout marked with its own CLAUDE.md pointer; its
  superseded uncommitted policy rewrite lifted to `deploy-backups/policy-decision-blocks-wip-20260913.patch`.
- 2026-09-13 — Doctor LaunchAgent install **blocked by the session sandbox**, not by choice. Verified the
  alert is silent on the current circle (`failing: []`). Install command is in the handover.

