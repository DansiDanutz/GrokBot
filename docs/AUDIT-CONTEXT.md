# DansLabTrader — System Map & Audit Context

> **Read this first.** This domain hosts **two independent paper-trading systems**.
> Most audit errors come from mixing their data. Attribute every number to exactly
> one system before drawing conclusions.

## The two systems

| | **A — Grid-bot desk (LIVE product)** | **B — Zmarty paper experiment (legacy research)** |
|---|---|---|
| Pages | `/` (paper desk), `/radar/` | `/control/` |
| Data files | `/data/autopilot.json`, `/data/radar.json` | `/data/report.json`, `/data/health.json`, `/data/analytics.json`, `/data/audits.json` |
| Account | 10,000 USDT, multi-symbol grid bots (KuCoin USDT-M perps) | 1,000 USDT, single-experiment strategy |
| Runtime (Mac) | `~/Sandbox/grokbot/autopilot/` — state.json, events.jsonl | `~/Sandbox/grokbot/zmarty-paper-runtime/` |
| Service | `com.danslab.trader-autopilot` → dashboard `:8875` | separate server on `:8873` |
| Deploy cadence | every ~5 min via `com.danslab.trader-publisher` | same publisher, static snapshots |
| Self-learning | **YES** — daily 07:00 review, learned-rules store, policy gates (this system's doctrine lives in the vault: `Operations/Trader Doctrine.md`) | NO |

**Never cross-reference**: an equity figure, trade count, or "audit" from `/data/report.json`
(Zmarty) tells you nothing about the grid desk and vice versa.

## Self-learning loop semantics (system A)

- Daily 07:00: analyze yesterday → proposals → **tier-1 auto-apply (gated)** → doctrine → vault.
- `deferred` is a **healthy** outcome, not a failure: sample gates (≥5 closes daily / ≥20 cumulative,
  ≥5 directional opens, ≥$10 estimated benefit, ≥95% kline coverage) exist so the system refuses to
  learn from noise. "0 applied" with a named reason is the loop working.
- Every run appends a trace line to `reports/rules-changelog.jsonl` (change *and* no-change, with
  `did_not_change_reason`). Every proposal disposition lands in `reports/proposals-ledger.jsonl`.
- Rule changes are evidence-cited, capped (3/day), and reversible — delete the rule from
  `~/Sandbox/grokbot/autopilot/learned-rules.json` to revert.

## How to verify a claim before reporting it

- "File X doesn't exist" → check the exact path (grid desk: `~/Sandbox/grokbot/autopilot/`; **not** the zmarty dir).
- "Service not registered" → `launchctl print gui/$(id -u)/com.danslab.<name>`; `state = not running`
  is NORMAL for calendar-scheduled jobs between fires.
- "Site is stale" → compare `shasum -a 256` of the live page vs the repo `paper_grid/paper.html`;
  publisher runs every ~5 min.
- "Data is fake" → the desk is a **paper simulator** on real KuCoin public market data; fills fire on
  10-second snapshot crossings (intra-tick whipsaws invisible — documented limitation). No exchange
  keys exist anywhere; no real orders are placed.

## Known limitations (system A, honest list)

- Fee model: taker 0.06% charged on all fills (real resting grids would get ~maker 0.02%) — conservative.
- Counterfactual "would-have-been" numbers are day-level estimates, labeled `estimated_benefit_usd`.
- Equity curve downsampling loses interior extremes after ~33h (KPIs unaffected).
- Closed-bot history capped at 20 (newest) — early-day position counts may undercount.

_Last updated: 2026-09-12 · maintained by the daily review pipeline (Kimi/Codex lanes)_
