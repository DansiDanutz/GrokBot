# Native paper trading review team

This is the original five-role acceptance record. See [version2](paper-team-v2.md)
for the expanded roster, two-room orchestration and data-source limitations.

This configuration defines five native Grok Bot assistants that review the
existing deterministic paper trader in the shared **Paper Grid Trading Team**
group. Five assistants do not add five execution slots. The core portfolio stays
at a maximum of five positions; the existing engine is the only executor.

Issue: https://github.com/DansiDanutz/GrokBot/issues/49

## Reproduce the instructions

For each native bot, combine [the standing contract](../config/paper-team/contract.md)
with exactly one role file below, in that order. Keep the full contract in every
bot's saved instructions; a link to a local file does not give a native bot access
to its contents. Use the role name as the native bot name and place all five in
the existing native group. No new runtime, dependency, credential, Telegram
poller, daemon, exchange connection or execution endpoint is required.

| Native bot | Role file | One-round responsibility |
| --- | --- | --- |
| Grid Desk Lead | [grid-desk-lead.md](../config/paper-team/roles/grid-desk-lead.md) | Ask for bounded notes; reconcile disagreement; synthesize once. |
| Data & Structure | [data-structure.md](../config/paper-team/roles/data-structure.md) | Validate public data freshness, structure and entry evidence. |
| Risk Sentinel | [risk-sentinel.md](../config/paper-team/roles/risk-sentinel.md) | Inspect published exposure, risk and data gaps; escalate once. |
| Performance Analyst | [performance-analyst.md](../config/paper-team/roles/performance-analyst.md) | Reconcile net outcomes and costs; preserve learning sample gates. |
| Research Scout | [research-scout.md](../config/paper-team/roles/research-scout.md) | Source external hypotheses within the existing strategy. |

A requested or scheduled round uses a single snapshot generation time as its identifier.
Each specialist returns one note, at most five bullets/150 words. The Lead
provides one final synthesis, labels missing evidence, and closes the round.
Group discussion is the hand-off channel; no assistant sends commands to the
paper engine. Existing schedules are not established by these files.

## Strategy and source precedence

The preserved baseline is **12–200 grids, 5x, strictly >1% per full grid pair on
allocated margin after two 0.06% fills**, confirmed chart bounds, and entry
buy/sell targets of LONG 40/60, SHORT 60/40 and NEUTRAL 50/50 with one order of
rounding tolerance. The selector chooses a supported feasible range; no eligible
setup is a valid outcome. No assistant may alter these rules or existing
liquidity, lot sizing, reserve, stop, funding and cooldown controls.

Current code references:

- [layout.py](../trader/radar/layout.py): MIN_GRIDS=12, MAX_GRIDS=200, entry splits,
  narrowest feasible confirmed support/resistance range selection.
- [spacing.py](../trader/radar/spacing.py): arithmetic interval, tick rounding,
  margin-return denominator, conservative worst pair and strict fee-adjusted floor.
- [constants.py](../trader/autopilot/constants.py): core five, direction cap four,
  5x, 1,000 USDT allocation and 200 USDT reserve per bot.
- [policy.py](../trader/autopilot/policy.py): admission, close rules, current-quote
  entry layout validation, per-bot net and portfolio accounting.
- [risk.py](../trader/autopilot/risk.py) and
  [engine.py](../trader/papergrid/engine.py): sizing and modeled execution costs.

Historical [radar prose](radar.md) still mentions a 70-grid minimum, and early
[autopilot specification](autopilot-spec.md) sections mention six positions and
3x trend leverage. Those statements do not override the current source and
confirmed baseline. This release documents the discrepancy and does not change
runtime strategy or rewrite historical specifications.

Entry split is an entry-time invariant. After fills, the live order ladder changes
and cannot by itself prove entry compliance. Missing original entry counts or
reconstructable original parameters are reported as unknown. The >1% pair-return
floor is not an account-return promise: funding, inventory and closing losses
can exceed completed-grid profit.

## Public evidence and a bounded observation

Use [autopilot JSON](https://danslabtrader.vercel.app/data/autopilot.json) and
[radar JSON](https://danslabtrader.vercel.app/data/radar.json). The legacy
`/control` and `/data/report.json` describe a separate experiment and must not
be mixed with autopilot balances, performance or settings. Every review compares
retrieval time against generation, publication and radar as-of timestamps.
A zero reported tick age can still describe an old public snapshot.

One read at **2026-09-13 00:19:27 UTC** returned autopilot generation
`1789258489493` and publication `1789258498629`. It reported five open and 17
closed bots, equity **9,871.9189 USDT** from 10,000 starting equity, net
**-128.0811 USDT**, gross grid profit **+507.4129 USDT**, **132.1111 USDT fees**
and 520 completed grids. These are an observation, not a current status guarantee.
The snapshot's `tick_age_s=0`, `kucoin_ok=true` and `recovery_pending=false` were
reported at generation; they do not independently prove live service health.
Radar generation was `1789257993137`, with as-of `1789257905582`.

The same published snapshot's `review_status` reported one planned proposal,
zero applied and one deferred: four directional opens were below a five-open
minimum, and FHEUSDTM data coverage was below 95%. The team must preserve that
learning HOLD. Five opens alone are not proof of statistical adequacy. The
snapshot also reported a four-hour minimum hold before non-risk closes and
trend alignment disabled. Those are observed deployed-overlay fields; their
producer is not independently verified by this documentation checkout. The
team neither changes them nor represents them as source-verified implementation.

## Native configuration acceptance

On 2026-09-13 the native Grok Bot UI visibly showed all five named bots as
members of **Paper Grid Trading Team**. Each received the standing strategy
boundaries and its specialist mandate. The first group round fetched the public
snapshots and produced an attributed Data & Structure report. Its incorrect
15-minute radar-staleness label was corrected against the deployed hourly
minute-05 schedule and >120-minute entry cutoff. Current realized grid rates
were also distinguished from expected rates at admission. The Lead-owned native routine **Paper grid team — hourly evidence review**
was verified Active in the app at 00:24 UTC, with the saved schedule **Every hour
at :15 (Europe/Bucharest)**. Its saved instructions require a material-change
check, one bounded specialist round in this group when needed, and one Lead
synthesis. Unchanged checks are silent; there are no per-specialist schedules.
The native routine showed **No runs yet**: configuration is verified, but its
first unattended invocation is not yet proven. The manual group review is the
initial hand-off acceptance test. Data & Structure replied at 00:21:50 UTC,
Risk Sentinel at 00:23:45, Research Scout at 00:24:50, Performance Analyst at
00:27:02 and Grid Desk Lead synthesized at 00:27:20. The result was advisory
REVIEW_REQUIRED, learning HOLD / INSUFFICIENT_EVIDENCE and NO_CHANGE to strategy.
The setup pass corrected normal-radar-lag, accounting double-counting and
source-time-versus-live-health wording. Original entry compliance remains unknown
where evidence is absent; a bounded history is not necessarily truncated.
These are advisory LLM reports, not newly measured trading performance.

See [external research](paper-team-research.md) for the original sources and
explicit limits on the X profitability claims and YouTube evidence.

## Release and verification limits

These files are reproducible instruction artifacts. Their existence does not
prove that the native app saved them, that all five bots joined the group, or
that a bounded group review ran successfully. Native installation, persisted
settings and group execution require separate UI evidence recorded by the
operator, as recorded above. An unavailable or rate-limited model must be reported as unavailable;
never claim its role completed because another assistant answered.

The assistants remain public-only and advisory. No credentials, computer/SSH
access, account access, orders, funds or engine/source/state writes are granted.
The saved schedule is verified above; its first unattended run remains pending. External research
is hypothesis-only. The observed sample cannot establish an edge or guarantee
winning trades. Changes to execution remain a separate reviewed work item.

Documentation verification should check all five role files, the shared contract,
relative links, preserved numeric invariants and the absence of operational
writes. The release passed syntax/configuration checks, 38 Node tests, 328 paper tests
and 287 trader tests (653 total), plus the staged-secret gate. Python emitted
database ResourceWarnings; no Python/runtime code was changed.
UI installation and runtime evidence must be reported separately from those
repository checks.

_Last verified: 2026-09-13_
