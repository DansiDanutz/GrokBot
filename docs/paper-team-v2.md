# Paper trading review team, version 2

Issue: https://github.com/DansiDanutz/GrokBot/issues/51

This extension adds dedicated X setup research, rule-lifecycle management and
technical interpretation to the existing five assistants. Grid Desk Lead remains
the orchestrator and sole owner of the existing hourly review routine. This
release configures native roles and adds a bounded sandbox evaluation bridge,
without replacing the trading runtime or strategy.

## Responsibilities and access

Prepend the current [standing contract](../config/paper-team/contract.md) to each
role's complete text when saving native instructions. Preserve existing bots and
the **Paper Grid Trading Team** core group and **Paper Grid Research & Data** room. The native UI stopped offering Add Member at six, so each room holds six.
Lead, Manager and Technical Interpreter bridge both rooms. The eight review roles are:

| Role | Responsibility |
| --- | --- |
| Grid Desk Lead | Orchestrator, task board, bounded hand-offs and one synthesis. |
| Data & Structure | Public-source freshness, chart boundaries and entry evidence. |
| Risk Sentinel | Exposure, deterministic-control evidence and risk contradictions. |
| Performance Analyst | Net outcomes, costs, coverage and sample measurements. |
| Research Scout | General primary-source research and methodology. |
| [X Setup Researcher](../config/paper-team/roles/x-setup-researcher.md) | Original X setup mechanics mapped to our strategy; no copied entries. |
| [Strategy Manager](../config/paper-team/roles/strategy-manager.md) | Evidence register, proposal lifecycle and rule-specific gate tracking. |
| [Technical Interpreter](../config/paper-team/roles/technical-interpreter.md) | Normalize and interpret KuCoin, CoinGlass and market-archive evidence. |

Existing **Dan’s Senior Developer** participates as an operations/data steward
through its existing Mac connection. Market/runtime inputs remain read-only: return
sanitized data summaries, source times, implementation versions and actual
reviewer/applier results. This does not establish new credentials, private account
access, a native Supabase connection or permission to edit production. The other
eight assistants consume public sources or the steward's attributed reports.
RD-001 delivered watchlist evidence; RD-002 corrected the selection to all five
open positions and passed the Technical Interpreter scope check at00:52:44UTC.
The user then authorized sanitized board and isolated experiment writes under
team-evidence/ and team-experiments/ only through that existing connection.

The eight assistants and optional existing steward do not expand execution
capacity. The current deterministic paper engine remains sole executor, with at
most five positions and four in one direction. Preserve chart-supported ranges,
12–200 grids, 5x, strictly >1% on allocated margin per complete pair after both
0.06% fills, and entry buy/sell targets LONG 40/60, SHORT 60/40, NEUTRAL 50/50
within one order of rounding. Existing liquidity, activity, sizing, reserve,
funding and exit controls remain authoritative. An empty eligible set is valid.

## Orchestrator board and bounded rounds

Maintain the board in the native conversation/report, not an engine state file:

| Field | Meaning |
| --- | --- |
| task_id | Stable round/task identifier. |
| owner | Named responsible role. |
| status | Reported task state, or UNKNOWN when no state was observed. |
| source_asof | Time and window of underlying evidence, not merely query time. |
| strategy_version | Version actually evidenced; UNKNOWN if unavailable. |
| evidence_uri | Direct public source or attributed steward/report reference. |
| dependency | Required upstream report, data or implementation. |
| blocker | Specific missing evidence or failure. |
| updated_at | UTC time the board entry was last updated. |

The Lead cannot see private agent state merely because a task was assigned.
Use UNKNOWN for missing reports. Ask each needed role for one concise evidence
note; avoid redundant research or unnecessary specialist calls on unchanged
snapshots. Permit one retry for a missing hand-off, then mark it BLOCKED and
synthesize with that limitation. Specialists do not trigger another round. Risk
or data contradictions stay unresolved rather than being outvoted. The Lead
closes the round with one summary and updates the existing routine; no duplicate
routine or separate perpetual loop is introduced by these documents.

## Learning and binding-rule boundary

Strategy Manager tracks:

`OBSERVED -> HYPOTHESIS -> TEST_READY -> SHADOW -> ELIGIBLE_TIER1 / DEFERRED / REJECTED -> APPLIED_VERIFIED / ROLLED_BACK`

The lifecycle describes evidence, not an engine API. Each proposal records its
source, version, cohort/window, metric, expected benefit, confounders, falsification
condition and dependencies. X mechanics can inform hypotheses within our current
strategy; author trade tips, marketing returns and unrelated methods cannot
become entries. A new formal rule remains SHADOW until separately implemented,
tested and reviewed. No new binding rule is qualified by this release.

Only an existing implemented tier-1 rule may become ELIGIBLE_TIER1, based on the
existing deterministic evaluator's generated evidence and exact rule-specific
gates. The existing deterministic applier is the only path for applying those
eligible rules. Native bot prose and fabricated proposal JSON must never be
passed off as generated evidence or write authority. Constants and risk gates
are outside the team's modification scope.

Read-only inspection on 2026-09-13 confirmed the deployed production sources
`/Users/davidai/ZCodeProject/GrokBot-prod/trader/review/rules.py`, `apply.py` and
`daily.py`. The current documentation checkout does not contain all of these
deployment overlays. That source-version distinction does not mean the deployed
reviewer is unavailable. Its `docs/AUDIT-CONTEXT.md` describes the existing daily
07:00 review and gated tier-1 application pipeline; inspecting code does not
independently prove that a particular scheduled run succeeded.

| Existing rule | Trigger and rule-specific evidence gates |
| --- | --- |
| `min_hold_hours_before_non_risk_close` | Proposes four hours when at least one premature close has >=$25 estimated missed benefit; requires >=5 daily closed bots, >=20 cumulative closed bots and >=$10 estimated rule benefit. Existing non-null hold is not re-proposed by this branch. |
| `require_trend_alignment` | Against-trend net must be negative and with-trend net greater than its absolute loss; requires >=5 daily directional opens and >=$10 estimated benefit; only proposed when not already active. |
| `symbol_cooldowns` | >=2 against-trend opens on a symbol that day and negative net, with symbol absent from the active cooldown map. Sets expiry to review date plus **seven days**. No separate $10 benefit or 5/20 closed-bot gate applies to this rule. |
| Coverage and application cap | A flagged day with <95% one-minute coverage on any traded symbol defers every candidate. The applier caps accepted changes at three per day and recomputes benefit for the two benefit-gated rules before writing. |

These are not a universal conjunction applied to every proposal. Cite the rule
ID, threshold, measured cohort/window and generated result. Missing gate evidence
yields DEFERRED; absence of a coverage flag alone is not proof of complete data.
Completed grids are not independent closed trades. Evaluate net after fees and
funding, drawdown and evidence coverage rather than gross grid activity.

The source [ordinary cooldown](../trader/autopilot/constants.py) is six hours
after a close; the learned symbol cooldown above is seven calendar days from
the review date. The distinction is verified in production `rules.py` through
`COOLDOWN_DAYS=7` and `timedelta(days=COOLDOWN_DAYS)`. Staged radar-flip hysteresis
is explicitly tier-2 advisory and never auto-applies; the Manager must not
promote it merely because its name is recognized by a schema.

APPLIED_VERIFIED requires a real, non-dry-run applier result, matching active
rule and post-change observation. The inspected dry-run branch writes review
status and counts while leaving the rules store unchanged; published counts
alone are insufficient. ROLLED_BACK requires actual rollback evidence. A
proposal, group agreement or planned application establishes neither state.

## Data inventory and source gaps

The following are **parent operator observations from 2026-09-13**, not independent
native-assistant connection tests. Refresh them before using them for a current
market judgment. SmartTrading is the relevant Supabase market archive;
ZmartyBrain identity/authentication data is excluded.

| Source checked | Newest evidence reported by the operator | Interpretation |
| --- | --- | --- |
| SmartTrading `indicator_scores` | 2026-08-24 | Stale for current indicators. |
| SmartTrading `symbol_intelligence_snapshots` | 2026-08-31 | Historical context only. |
| SmartTrading `RiskMetricLiveData` | 2026-08-31 | Name does not establish live freshness. |
| SmartTrading general CoinGlass data | 2025-10-28 | Stale archive; do not use as current signal. |
| BTC liquidation history | 2026-09-12 | More recent history, with exact window/coverage still required. |
| Local market SQLite, checked 00:37 UTC | Approximately 24.24 million candles across 522 symbols; latest candle 00:31, OI/tickers 00:32, book 00:36 UTC | Local market collection is more recent than the named Supabase tables. Maximum timestamp does not prove every symbol is fresh. |
| Local CoinGlass executed-liquidation aggregates | 594 hourly rows across 11 symbols; last completed interval begins 23:00 UTC September 12 and ends 00:00 September 13 | Historical executed liquidations; identify interval endpoints and completion before comparing. |
| CoinGlass entitlement, checked September 11 | STARTUP: history worked; heatmap/order endpoints returned “Upgrade plan” | No verified future heatmap/order product access. No upgrade is requested. |

Normalize venue, symbol/contract, quote currency, units, interval bounds and UTC
time before joining sources. KuCoin-specific markets and cross-venue CoinGlass
aggregates have different scopes. Executed-liquidation clusters are not future
heatmaps or resting orders. Avoid counting correlated price, volume, OI and
liquidation measurements as independent confirmations. Query success, HTTP
reachability or a recent report time cannot establish data recency or coverage.

The RD-002 local-data handoff succeeded. Nothing here repairs stale ingestion
or installs a direct native Supabase connector. A separate existing-connector
Codex feed now writes sanitized historical evidence for the steward to consume;
see [feed contract](../config/paper-team/supabase-data-bridge.md). Native readback
and first scheduled refresh are distinct acceptance facts.

## Manual acceptance

The parent operator owns native installation and acceptance evidence. Do not
claim the new roles are installed, the extended routine ran, a bridge works or a
rule was applied from the existence of these files. Record separately:

1. The three new named bots persist their standing contract and role instructions;
   the existing Lead persists its orchestrator mandate and existing routine update.
2. The core room shows Lead, Manager, Data & Structure, Technical Interpreter, Risk Sentinel and Performance Analyst. The research/data room shows Lead, Manager, Technical Interpreter, X Setup Researcher, Research Scout and existing Senior Developer.
3. One bounded round shows X evidence with falsification, normalized technical
   evidence or an explicit source gap, and a Manager proposal remaining SHADOW or
   DEFERRED when it lacks implemented/gated support.
4. The Lead board attributes actual replies, retries a missing hand-off at most
   once, marks unavailable state and closes with one synthesis.
5. Any claimed existing tier-1 application is backed by generated evaluator and
   applier records plus verified active state; otherwise no application is claimed.

Repository checks validate documentation and links. They cannot validate native
permissions, private-source access, schedule execution or the profitability of a
strategy. The sandbox bridge adds code and offline tests without new dependencies
or engine/rule-state edits. Its generated evidence does not authorize application.


## Observed setup and additional limits

All three new native bot names and both six-member room rosters were observed on
2026-09-13. Lead acknowledged its orchestrator role and was instructed to persist
a task board across both rooms. The Lead acknowledged the updated hourly routine at00:55:50UTC. RD-002 completed
at00:52:32UTC and its symbol-selection check passed at00:52:44UTC. A once-daily 09:15 research pass
is included in that single routine, not a separate autonomous loop.

Fresh Supabase SELECT MAX checks on September13 confirmed exchange_ti_snapshots
and indicator_scores latestAugust24, exchange_scores_v4 July29, symbol_intelligence
and RiskMetricLiveData August31, general coinglass October28 2025, while BTC daily
liquidations extend to September12. zmarty-axi health returned HTTP503. Neither
stale ingestion nor API health was repaired by configuring assistant roles.
Native Manager decisions are not currently a control input to the pre-existing
07:00 rule pipeline. Novel-rule auto-application is not connected. Record proposed
Manager stages separately from observed runtime changes; do not claim otherwise.

_Last verified: 2026-09-13_


X onboarding reported ACCESS_BLOCKED: no original public X grid-setup source was
retrieved. Parent searches also failed to return an original X grid post; an
attempt to read a previously known public post via Jina returned403. No logged-in
X browser surface was available. This is a source-access limitation, not proof
that no useful setups exist. No X source or profitable copied setup is claimed.
Public papers/official documentation remain available to Research Scout. One
additional primary paper located is [Dynamic Grid Trading Strategy](https://arxiv.org/abs/2506.11921)
(Chen, Chen and Jang, June2025); its historical BTC/ETH reset-strategy results
are a research lead, not validation of our leveraged KuCoin policy.


## Autonomy extension

The existing engine was independently observed running at approximately00:55UTC:
5 paper positions, fresh heartbeat, no recovery pending and recent decision/fill/
grid events. Radar and CoinGlass scheduled jobs had successful runs. The daily
07:00 review job was installed with zero launchd runs at inspection; prior manual
real evaluator/applier runs were recorded. Do not call that scheduled invocation
proven until it runs. Its last real result was a gated no-change, not a new apply.

The Lead now checks board/overdue work/daily research/source health before the
quiet branch, checks radar core/bench separately from open_bots, and tracks new
needs with owners and acceptance criteria. Manager records generated outcomes;
novel strategies and missing capabilities become measurable engineering needs.
See [autonomy acceptance](paper-team-autonomy.md). A saved board and routine alone
cannot establish restart recovery or demonstrated profitability.


SB-001 native steward read was observed at01:02:27UTC: PARTIAL_HISTORICAL,
DOT-only archived score row, four missing open symbols, zero bounded indicator
rows. Native transport is working through the existing Mac connection; ingestion
is still stale. Board.json was independently read from the approved sandbox after
native creation at00:56:41UTC, preserving prior records. Actual restart recovery
remains untested. The native Lead saved the canonical HOLD/REVIEW/ESCALATE advisory
vocabulary at00:59:02UTC; task, proposal and engine-effect states stay separate.

The isolated [request bridge](paper-team-bridge.md) completed a real September12
trend review at01:00:50UTC, yielding DEFERRED and applied:false with actual gate
metrics. The native team must acknowledge those artifacts separately. The bridge
cannot apply novel rules or bypass the independently running daily writer.


Native operations created request NATIVE-TREND-20260913-01 through the installed
worker. Its actual result artifact completed01:08:51.766807UTC, DEFERRED,
applied:false. The generated metrics match the same sample/coverage hold. Final
full verification passed677tests (38Node+328paper+287trader+24bridge), and the
staged-secret gate passed267trackedfiles. Independent review rechecked cached
receipt integrity and isolated source imports with no remaining concrete blocker.
