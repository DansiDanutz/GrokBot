# JEV / TypeSafe integration map

Last verified: 2026-09-19

## Non-negotiable boundary

`danslabtrader` and `DansLab-JEV` are different systems with different risk.

| System | Runtime | Money mode | TypeSafe role |
| --- | --- | --- | --- |
| GrokBot / danslabtrader | `~/ZCodeProject/GrokBot`, active paper runtime under `~/Sandbox/grokbot/` | Paper only. Public KuCoin data drives simulated fills; it must not place exchange orders. | Candidate for advisory scoring, classification, and audit triage. A TypeSafe answer must never become an exchange order. |
| DansLab-JEV | deployed source `~/DansLab/jev-maker`, LaunchAgent `com.danslab.jev` | Real-money KuCoin Futures. It can place authenticated post-only orders and IOC reduce-only exits. | The directional judgment engine. Deterministic code retains fee gates, confidence gates, position limits, stops, and order execution. |

The Codex development checkout `~/ZCodeProject/DansLab-JEV` is not the deployed
runtime. Do not copy it over `~/DansLab/jev-maker`; the deployed checkout is newer
and is the source used by launchd.

## Current JEV implementation

`TypeSafeJevModel` in `jev/jev_model.py` calls:

```text
POST https://api.typesafe.ai/v1/systemone
model: jev-latest
question: one Choice named direction
criteria: buy | sell
```

It sends a compact market state containing price, spread, book imbalance and
depth, recent returns and mids, taker-flow summary, recent prints, allowed sides,
and 24-hour range context. The response is normalized into `buy`/`sell`
probabilities plus TypeSafe's Choice confidence. The caller records latency and
input-token cost.

JEV does not own risk policy. `JevBrain` applies the spread-versus-fees test and
the calibrated-confidence floor. `jev_live_run` owns position caps, stop/target,
maximum hold, maker-first exit, IOC fallback, session stop-loss, and audit events.
The supervisor owns arming, slot allocation, doctor verdicts, and process recovery.

## Verified state

- TypeSafe console was authenticated in Chrome.
- Last-24-hour console usage at inspection time: 7,882 requests, 10,283,716 input
  tokens, estimated spend $0.4217 at $0.042/MTok input; output was free.
- One synthetic `python3 -m jev.jev_model jev` call returned a valid typed Choice
  in 842.6 ms. This test did not invoke exchange execution.
- `com.danslab.jev` was loaded, but the master trading state was disarmed after a
  session stop-loss. Inspection did not re-arm it.
- GrokBot trader/autopilot services were running against paper state and public
  market data. No live-exchange adapter exists in the GrokBot contract.

## Integration policy

Use TypeSafe when the missing operation is a narrow semantic judgment over known
state: route, rank, select, score, verify, or decide whether a condition holds.
Keep arithmetic, exact lookups, permissions, side effects, and risk limits in code.

For GrokBot, introduce TypeSafe in shadow mode first:

1. Ask independent judgments in one request (entry suitability, regime, range
   quality, and evidence sufficiency) and store the raw distributions.
2. Do not alter paper decisions during the shadow period. Compare judgments with
   realized forward returns, drawdown, grid fills, and existing deterministic
   scores.
3. Calibrate thresholds on recorded outcomes. Promote only a demonstrated signal
   into paper policy, behind an explicit feature flag and deterministic fallback.
4. Keep the repository paper-only. Any future live adapter requires a separate
   owner-approved phase and must not reuse paper authorization.

For Mac Studio workflows, prefer these high-value uses:

- routing tasks to the correct harness, skill, or model;
- ranking retrieved notes or candidate files before loading expensive context;
- verifying whether evidence supports a claim before escalating to a reasoning model;
- classifying alerts and deciding which need a human;
- extracting one value from a code-generated candidate list;
- batching independent questions over the same state to reduce latency and tokens.

Do not use JEV as a replacement for a generative coding/reasoning model, a secret
store, an authorization layer, financial risk policy, or a source of facts absent
from the supplied state.

## Token and cost discipline

The observed average was about 1,305 input tokens per request. Before raising call
volume, benchmark a compact state against the current state on the same labeled
events. Remove duplicated prose and unused order-book detail only if Brier score,
direction accuracy, and confidence calibration do not regress. Batch questions
that share state; do not add speculative questions whose answers are never used.

Every integration must log request purpose, model alias, latency, input tokens,
answer distribution, confidence when present, fallback reason, and downstream
action. Never log the API key or raw authorization headers.

## Global agent availability

The canonical skill is `~/.agents/skills/typesafe-ai`. Symlinks expose it to
Claude, Codex, Zcode, OpenCode, Pi, and Kimi so each harness reads the same current
instructions. The skill is guidance, not a globally enabled API proxy; individual
applications still need an explicit, server-side credential path and an audited
call site.

