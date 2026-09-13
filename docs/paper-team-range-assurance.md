# Range assurance and Paper Desk Secretary

Issue: https://github.com/DansiDanutz/GrokBot/issues/52

This document records the user's range/cost/exit mandate and the corresponding
team responsibilities. The code corrections are **DEPLOYED_VERIFIED** at production revision
`f02703afcb57278d96b9c99063663cdb2f22a2b2` after the explicitly requested release.
Updating native instructions or this document does not change engine behavior.

## Contact and ownership

[Paper Desk Secretary](../config/paper-team/roles/paper-desk-secretary.md) is the
main user contact for results, trades, range/grid math, quality findings and
unfinished requests. Grid Desk Lead remains operational orchestrator. Existing
Dan’s Senior Developer is the sole shared-board writer. The participant count
is ten; the two existing six-member rooms remain unchanged. The Office room
with Secretary, Lead and steward has observed hand-offs and private board
readback; the native steward acknowledgment and Lead closure are verified. The parent verified the full Office hand-off, native acknowledgment and Lead
closure at the evidence times below.

After a material round, Lead obtains the steward's atomic board write/readback
receipt, then sends one summary and unresolved user requests to Secretary.
Preserve request/task ID, owner, assigned_at, updated_at, source_asof, actual
status, evidence reference, board version and final_response status/reference.
Secretary gives one evidence-backed final response and returns its reference
through Lead for steward persistence. Missing updates stay UNKNOWN. A missing
receipt becomes BOARD_PERSIST_BLOCKED; an urgent failure may be communicated
once with that limitation rather than suppressed. Unchanged cycles stay quiet.

The [existing controller](../config/paper-team/controller.md) remains the single
Codex heartbeat at :15; the native timer stays PAUSED. Secretary receives no
new timer, private access, execution rights or board-write authority. Existing
engine execution and the daily 07:00 gated tier-1 rule writer retain ownership.

## Mandate versus observed implementation

| Requirement | Current evidence and required acceptance |
| --- | --- |
| Historical chart-supported range | Complete 168-hour history proof, freshness checks, new-entry dossiers and durable SHA archives are DEPLOYED_VERIFIED. A `range_verified` flag alone does not prove the new checks. |
| Many viable grids | Prefer the highest feasible count within confirmed support/resistance, 12–200 grids, 5x and strict cost floor. Do not widen bounds to fit more grids or force 200. Existing narrowest-feasible-range selection does not authorize global count maximization across invented ranges. |
| Futures-bot fill costs | The parent-observed production 724 behavior used 0.02% maker / 0.06% taker. This conflicts with the mandated KuCoin futures-bot model, which requires 0.06% for both. Correction is DEPLOYED_VERIFIED at f02703; old results retain their original accounting epoch. |
| Boundary risk exit | A valid observed price at or below support or at or above resistance must cause a risk exit even at a loss, without a minimum-hold delay, extra step or repeated-outside requirement. Runtime/exception-path fixes are DEPLOYED_VERIFIED; each actual closure still needs its own event evidence. |
| Inactive-bot rotation | NOT_IMPLEMENTED. It needs a validated better setup and comparable net switching benefit after costs, subject to applicable minimum hold. Inactivity or a high-ranked candidate alone is insufficient. |
| Optional liquidation clusters | Only actual price-level/time-axis heatmap evidence can support a cluster claim. Local hourly executed-liquidation totals do not contain those levels. No new heatmap entitlement is installed. |

KuCoin's official FAQ states that futures-bot maker and taker fills both use a
fixed 0.06% fee. This is the futures-bot simulation target, rather than a claim
about every custom futures account's fee tier. [KuCoin trading-bot fees](https://www.kucoin.com/support/21960469554201)

CoinGlass describes its aggregated heatmap as modeled liquidation levels and
provides price-axis data; the checked endpoint lists Professional and Enterprise
availability, excluding STARTUP. A documented product does not prove this
account can fetch it. No plan purchase or upgrade is part of this work.
[CoinGlass aggregated liquidation heatmap](https://docs.coinglass.com/reference/liquidation-aggregate-heatmap)

## Per-setup range dossier

Data & Structure owns the dossier; Technical Interpreter explains it; Risk and
Performance check it independently before Lead sends the reconciled result to
Secretary. Required evidence is:

- Symbol/venue/contract, direction, evidence ID, source revision and observation
  time; candle timeframes, window endpoints and latest completed-bar time.
- Complete-bar count, coverage and gaps, source age and validation outcome.
  Exclude the unfinished current bar from completed-history evidence. Missing
  inputs remain missing; do not invent thresholds or values to mark a pass.
- Confirmed support/resistance with historical touches and the method/source
  that produced them; inward tick rounding and why this supported range was chosen.
- Entry quote/time, original buy/sell split, arithmetic grid count/interval and
  conservative minimum pair return. Today's ladder cannot prove the entry split.
- Optional heatmap source/model, venue scope, observation time, price/time axes
  and clustered levels. Historical executed totals are separate context; absent
  heatmaps remain UNKNOWN and do not invalidate otherwise sufficient chart data.
- Fee/accounting version, seed inventory and cost, modeled grid fees, current
  funding observation/schedule, exit-cost estimate and uncertainty assumptions.

The existing limits stay intact: LONG entry buy/sell 40/60, SHORT 60/40,
NEUTRAL 50/50 with one-order tolerance, maximum five positions and four in one
direction, plus all existing liquidity, sizing, reserve and risk checks.

## Cost interpretation and exits

For one modeled pair, quantity `q`, buy `b`, sell `s` and fee `f=0.0006`,
fee-net pair profit is `q * (s - b) - f * q * (b + s)`. Divide by allocated
margin `q*b/5` for LONG or `q*s/5` for SHORT; use the conservative side for
NEUTRAL. The minimum throughout the range must be strictly above 1% after tick
rounding. This does not include every lifecycle cost or guarantee a winning bot.

Seed/exit fees, signed funding, unrealized losses and any modeled slippage also
contribute to net results. Future funding and fills are unknown: label assumptions,
show scenarios where useful, and never convert a fee-net pair floor into a
future net-profit guarantee. Preserve original historical accounting labels;
revised fee assumptions need separate comparison rather than silent rewrites.

Risk reports a boundary observation, event and closure as separate evidence.
The mandate is touch-or-breach behavior: `price <= support` or
`price >= resistance`, including exact equality. A stale/missing quote does
not prove a current breach, and gaps may prevent a fill exactly at the boundary.
Exception-path acceptance must show that unrelated missing admission metadata
cannot silently suppress a valid observed risk exit. The executor, not a chat
assistant, performs any close.

Non-risk rotation must compare the incumbent with a validated replacement over
a comparable horizon, include close/reseed fees, funding and execution
uncertainty, and honor minimum hold. It remains a needs/backlog item and shadow
hypothesis until implemented, tested and independently evidenced. It must never
be conflated with mandatory boundary risk exits.

## Release acceptance and continuing evidence requirements

The parent owns code and native acceptance. Record each state separately:

1. Secretary instructions, Office roster and Lead/steward routing are saved and
   read back; one material request yields a board receipt and final answer reference.
2. Corrected source passes targeted tests for stale/incomplete candle histories,
   range provenance, rounded worst-pair economics and both-side fees including seed
   and exit. Validate boundary exits in ordinary and exception/data-gap paths.
3. An explicit deployment receipt identifies the actual running revision and
   fee/accounting version. Repository tests alone do not prove that deployment.
4. Runtime evidence shows the revised dossier and observed exit behavior without
   generating a false claim from a historical or stale snapshot.
5. Rotation stays NOT_IMPLEMENTED unless its own code/test/deployment evidence is
   supplied. No novel rule becomes executable from a Secretary or Manager message.

These documentation changes add no timer, dependency, private connector, real
order or strategy-state write. Their purpose is to make the mandate, detected
defects, implementation status and final user answer traceable.


## Office acceptance evidence — 2026-09-13

Parent-verified evidence: Secretary QA at 01:53:12 UTC; Lead relay at 01:53:53 UTC;
steward Office board entry written at 01:54:21 UTC, native acknowledgment at
01:54:41 UTC, board readback at 01:55:00 UTC and Lead closure at 01:55:22 UTC.
The Office hand-off is verified complete. Current-cost dossier QA02 was sent at
01:57:44 UTC; Secretary returned the five-position table at 01:58:50 UTC and the
steward persisted its result at 01:59:05 UTC (native acknowledgment 01:59:23 UTC).
This verifies the user-request → Lead → steward → Secretary → record path.


A parent read-only audit at 01:55:41 UTC found an active legacy position whose
worst-pair return fails the mandated 0.06%-per-fill calculation; the other four
reviewed positions passed that pair check. No private position values are included
here. Disclose each active position's fee/accounting epoch and validation basis;
do not imply a corrected model retroactively changed fills or that a legacy
position automatically meets the current entry floor. Any new low-volatility
rotation approach remains a staged strategy need until implemented and evaluated.


## Verified range-assurance release — 2026-09-13

PR #53 merged to `f02703afcb57278d96b9c99063663cdb2f22a2b2`, the verified production
HEAD. Tested head: `6062d392a8680c67f5dd0ae4d85facc6375b3fec`. Independent review,
Mac/Linux CI, 38 Node + 335 paper + 422 trader tests (795 total), and a 270-file
secret scan passed. The controlled same-label writer/dashboard cutover preserved
prior bot IDs, ledger start and the unrelated dirty production file.

At 02:18:50 UTC the parent verified HTTP 200 for local health/autopilot/radar,
a fresh heartbeat, kucoin_ok=true, recovery_pending=false, zero pending funding
and recovery items, and archive status COMPLETE. New entries require complete
168-hour history proof, 0.06% both-side bot fees, maximum viable grid selection,
range dossiers, durable SHA archives and cost scenarios. This is source-time
acceptance evidence, not an indefinite guarantee of live health.

Legacy dossiers currently remain MISSING; do not manufacture historical entry
proof. Invalid legacy pair-floor handling uses PROFILE_UPDATE with the existing
non-risk hold. Post-rollout observation included a held legacy position under
that rule. Boundary touch/breach risk exits bypass that non-risk hold. Preserve
historical fee epochs and distinguish these two exit reasons.

The bridge was explicitly repinned to f02703 and context verified at 02:18:06 UTC;
its installed worker was unchanged. Old 724-source evaluation receipts remain
historical. Sanitized deployment-range-assurance.json and engineering-status.json
were prepared under team-evidence and the heartbeat knows this release. Native
The steward read the new release artifacts and persisted the implementation
closures at 02:24:22 UTC. Secretary returned the corrected direct-chat answer at
02:24:42 UTC. Native release handoff is verified; this is not a learned-rule win.

Rotation remains NEEDS_IMPLEMENTATION, heatmap access remains UNAVAILABLE and
the first unattended Codex-controller completion remains unverified. This
explicitly requested release does not widen the scheduled 10:15 engineering
phase: its no-auto-deploy scope and controlled repin ownership remain unchanged.


QA03 checked historical support/resistance for the then-open five positions:
all recorded bounds matched at 02:08:40 UTC; Secretary responded at 02:13:58 UTC
and the result was persisted at 02:14:13 UTC. Matching historical bounds does
not prove a complete modern dossier. Direct Secretary front-door QA04 produced
a real reply at 02:14:50 UTC, board persistence at 02:17:04 UTC and native
acknowledgment at 02:17:23 UTC. The later f02703 release packet was independently
read and recorded by the steward at 02:24:22 UTC; Secretary updated the user
answer at 02:24:42 UTC.
